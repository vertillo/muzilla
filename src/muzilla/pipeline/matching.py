"""Match orchestration: the seam between the DB (`Track`/`TrackGroup`
rows) and the pure, network-free `matching/engine.py` — converts rows
to `TrackMeta`, fetches candidates via the provider set, scores them,
and (on request) stages a `match_proposal` ChangeSet from one chosen
candidate. Matching itself never touches the DB or the network; this
module is where those two worlds meet.

Lives in `muzilla.pipeline` (not `services`) so both `services/` (an
API request staging a match on demand) and `jobs/` (a background
import job doing the same thing at scale) can call it directly without
a layering violation — `services` sits above `jobs`/`pipeline`, so code
only `services` could reach would be unusable from a job handler.
`services.matching` re-exports this module's public names so existing
`api`/`cli` imports are unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.config.schema import PathsConfig
from muzilla.db.models import ChangeSet, Track, TrackGroup
from muzilla.domain import fields as field_registry
from muzilla.domain.metadata import TrackMeta
from muzilla.matching.candidates import (
    ProviderSearchOutcome,
    ScoredCandidate,
    ScoreSignal,
    retrieve_and_hydrate,
)
from muzilla.matching.engine import (
    AlbumMatchResult,
    SingletonMatchResult,
    propose_for_group,
    propose_for_singleton,
    track_pair_distance,
)
from muzilla.matching.filename import parse_filename
from muzilla.matching.track_align import align_tracks
from muzilla.providers.base import ProviderRef, ReleaseCandidate, ReleaseQuery
from muzilla.providers.set import ProviderSet

# Fields carried by TrackMeta/Track that a ReleaseCandidate's tags can
# plausibly set on a track — everything else (technical/probe fields,
# audio analysis, extra_tags) is left untouched by a match_proposal.
_APPLICABLE_TRACK_FIELDS = {
    f.name for f in field_registry.FIELDS.values()
    if f.editable and f.category.name not in ("TECHNICAL", "AUDIO_ANALYSIS")
}


def _track_to_meta(track: Track) -> TrackMeta:
    kwargs = {
        f.name: getattr(track, f.name)
        for f in field_registry.FIELDS.values()
        if hasattr(track, f.name)
    }
    return TrackMeta(**kwargs)


def _track_to_match_meta(track: Track) -> TrackMeta:
    """Use parsed filename evidence only where scanned tags are absent."""
    meta = _track_to_meta(track)
    parsed = parse_filename(track.filename)
    return replace(
        meta,
        title=meta.title or parsed.title,
        artist=meta.artist or (parsed.artist if parsed.confidence >= 0.9 else None),
    )


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """One ranked (source, release) row for the candidate-picker UI
    — a plain dataclass, never a ReleaseCandidate
    directly, so api/cli never need to import muzilla.providers."""

    source: str
    ref_id: str
    album: str | None
    album_artist: str | None
    year: int | None
    label: str | None
    catalog_number: str | None
    track_count: int | None
    candidate_type: str
    representative_title: str | None
    representative_artist: str | None
    representative_position: int | None
    representative_duration_ms: int | None
    cover_url: str | None
    distance: float
    adjusted_distance: float
    score_signals: tuple[ScoreSignal, ...]
    is_duplicate_of: tuple[int, ...]
    corroborated_by: tuple[str, ...]
    rejection_reason: str | None = None


def candidate_row(sc: ScoredCandidate) -> CandidateRow:
    c = sc.candidate
    return CandidateRow(
        source=c.source,
        ref_id=c.ref.id,
        album=c.album,
        album_artist=c.album_artist,
        year=c.year,
        label=c.label,
        catalog_number=c.catalog_number,
        track_count=c.track_count,
        candidate_type=c.candidate_type,
        representative_title=sc.representative_track.title if sc.representative_track else None,
        representative_artist=sc.representative_track.artist if sc.representative_track else None,
        representative_position=sc.representative_track.position if sc.representative_track else None,
        representative_duration_ms=sc.representative_track.duration_ms if sc.representative_track else None,
        cover_url=c.art_refs[0].url if c.art_refs else None,
        distance=sc.distance,
        adjusted_distance=sc.adjusted_distance,
        score_signals=sc.signals,
        is_duplicate_of=sc.is_duplicate_of,
        corroborated_by=sc.corroborated_by,
        rejection_reason=sc.rejection_reason,
    )


@dataclass(frozen=True, slots=True)
class GroupMatchProposal:
    group_id: int
    candidates: tuple[CandidateRow, ...]
    auto_applicable: bool
    needs_confirmation: bool
    provider_outcomes: tuple[ProviderSearchOutcome, ...] = ()
    rejection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class TrackMatchProposal:
    track_id: int
    candidates: tuple[CandidateRow, ...]
    auto_applicable: bool
    needs_confirmation: bool
    provider_outcomes: tuple[ProviderSearchOutcome, ...] = ()
    rejection_reason: str | None = None


async def propose_group_candidates(
    session: Session, provider_set: ProviderSet, group_id: int, limit_per_provider: int = 5
) -> GroupMatchProposal:
    """Fetches and ranks release candidates for an album group. Read-only
    — does not stage anything; call `stage_group_match` with a chosen
    `ref_id` to actually create the ChangeSet."""
    group = session.get(TrackGroup, group_id)
    if group is None:
        raise ValueError(f"group {group_id} not found")

    query = ReleaseQuery(
        album=group.album,
        album_artist=group.album_artist,
        track_count=len(group.tracks) or None,
        year=group.year,
        barcode=group.barcode,
        catalog_number=group.catalog_number,
    )
    return await search_group_candidates(
        session, provider_set, group_id, query, limit_per_provider=limit_per_provider
    )


async def search_group_candidates(
    session: Session,
    provider_set: ProviderSet,
    group_id: int,
    query: ReleaseQuery,
    *,
    limit_per_provider: int = 5,
    include_rejected: bool = False,
) -> GroupMatchProposal:
    """Run an explicit normalized query against a group's local evidence.

    Manual search intentionally shares the same retrieve/hydrate/rank path as
    automatic matching; only the query source changes.
    """
    group = session.get(TrackGroup, group_id)
    if group is None:
        raise ValueError(f"group {group_id} not found")

    tracks = list(group.tracks)
    retrieval = await retrieve_and_hydrate(
        query,
        provider_set.metadata,
        search_limit=max(12, limit_per_provider * 3),
        hydrate_limit=limit_per_provider,
    )

    local_metas = [_track_to_meta(t) for t in tracks]
    result: AlbumMatchResult = propose_for_group(
        local_metas,
        list(retrieval.candidates),
        album=group.album,
        album_artist=group.album_artist,
        year=group.year,
        label=group.label,
        catalog_number=group.catalog_number,
        barcode=group.barcode,
    )
    return GroupMatchProposal(
        group_id=group_id,
        candidates=tuple(candidate_row(sc) for sc in result.ranked if include_rejected or not sc.rejected),
        auto_applicable=result.decision.auto_applicable,
        needs_confirmation=result.decision.needs_confirmation,
        provider_outcomes=retrieval.provider_outcomes,
        rejection_reason=result.decision.rejection_reason,
    )


async def propose_track_candidates(
    session: Session, provider_set: ProviderSet, track_id: int, limit_per_provider: int = 5
) -> TrackMatchProposal:
    """Fetches and ranks recording candidates for a singleton track."""
    track = session.get(Track, track_id)
    if track is None:
        raise ValueError(f"track {track_id} not found")

    parsed = parse_filename(track.filename)
    query = ReleaseQuery(
        title=track.title or parsed.title,
        artist=track.artist or parsed.artist,
        album_artist=track.album_artist,
        duration_ms=track.duration_ms,
        isrc=track.isrc,
    )
    return await search_track_candidates(
        session, provider_set, track_id, query, limit_per_provider=limit_per_provider
    )


async def search_track_candidates(
    session: Session,
    provider_set: ProviderSet,
    track_id: int,
    query: ReleaseQuery,
    *,
    limit_per_provider: int = 5,
    include_rejected: bool = False,
) -> TrackMatchProposal:
    """Run an explicit normalized query against a singleton's local evidence."""
    track = session.get(Track, track_id)
    if track is None:
        raise ValueError(f"track {track_id} not found")

    retrieval = await retrieve_and_hydrate(
        query,
        provider_set.metadata,
        search_limit=max(12, limit_per_provider * 3),
        hydrate_limit=limit_per_provider,
    )

    local_meta = _track_to_match_meta(track)
    result: SingletonMatchResult = propose_for_singleton(local_meta, list(retrieval.candidates))
    return TrackMatchProposal(
        track_id=track_id,
        candidates=tuple(candidate_row(sc) for sc in result.ranked if include_rejected or not sc.rejected),
        auto_applicable=result.decision.auto_applicable,
        needs_confirmation=result.decision.needs_confirmation,
        provider_outcomes=retrieval.provider_outcomes,
        rejection_reason=result.decision.rejection_reason,
    )


def release_to_track_edits(candidate: ReleaseCandidate, local_track_index: int | None) -> list[FieldEdit]:
    """Builds the FieldEdit list for one track from a chosen release —
    only fields the candidate actually supplies are touched, per the
    "one release, one source" rule: the whole
    changeset's tags come from this one candidate, never mixed with
    another source's data."""
    edits: list[FieldEdit] = []
    if candidate.album is not None:
        edits.append(FieldEdit(field="album", new_value=candidate.album))
    if candidate.album_artist is not None:
        edits.append(FieldEdit(field="album_artist", new_value=candidate.album_artist))
    if candidate.year is not None:
        edits.append(FieldEdit(field="year", new_value=candidate.year))
    if candidate.original_year is not None:
        edits.append(FieldEdit(field="original_year", new_value=candidate.original_year))
    if candidate.label is not None:
        edits.append(FieldEdit(field="label", new_value=candidate.label))
    if candidate.catalog_number is not None:
        edits.append(FieldEdit(field="catalog_number", new_value=candidate.catalog_number))
    if candidate.barcode is not None:
        edits.append(FieldEdit(field="barcode", new_value=candidate.barcode))
    if candidate.country is not None:
        edits.append(FieldEdit(field="country", new_value=candidate.country))
    if candidate.media is not None:
        edits.append(FieldEdit(field="media", new_value=candidate.media))
    if candidate.mb_release_id is not None:
        edits.append(FieldEdit(field="mb_release_id", new_value=candidate.mb_release_id))
    if candidate.discogs_release_id is not None:
        edits.append(
            FieldEdit(field="discogs_release_id", new_value=candidate.discogs_release_id)
        )
    if candidate.deezer_album_id is not None:
        edits.append(FieldEdit(field="deezer_track_id", new_value=candidate.deezer_album_id))

    if local_track_index is not None and 0 <= local_track_index < len(candidate.tracks):
        t = candidate.tracks[local_track_index]
        edits.append(FieldEdit(field="title", new_value=t.title))
        if t.disc_number is not None:
            edits.append(FieldEdit(field="disc_no", new_value=t.disc_number))
        edits.append(FieldEdit(field="track_no", new_value=t.position))
        if t.isrc is not None:
            edits.append(FieldEdit(field="isrc", new_value=t.isrc))
        if t.mb_track_id is not None:
            edits.append(FieldEdit(field="mb_track_id", new_value=t.mb_track_id))
        if t.mb_recording_id is not None:
            edits.append(FieldEdit(field="mb_recording_id", new_value=t.mb_recording_id))

    return edits


def candidate_edits_for_group(group: TrackGroup, candidate: ReleaseCandidate) -> dict[int, list[FieldEdit]]:
    """Map one hydrated candidate to every track in a group.

    This is shared by legacy staging and the ReviewBundle adapter so provider
    metadata is translated in exactly one place.
    """
    tracks = list(group.tracks)
    local_metas = [_track_to_meta(track) for track in tracks]
    alignment = align_tracks(local_metas, list(candidate.tracks), track_pair_distance)
    candidate_index_by_track_id = {
        tracks[item.local_index].id: item.candidate_index
        for item in alignment
        if item.local_index is not None
    }
    return {
        track.id: release_to_track_edits(candidate, candidate_index_by_track_id.get(track.id))
        for track in tracks
    }


def candidate_edits_for_track(track: Track, candidate: ReleaseCandidate) -> list[FieldEdit]:
    """Map one hydrated candidate to a singleton using the ranking alignment."""
    local_meta = _track_to_meta(track)
    best_index: int | None = None
    if candidate.tracks:
        best_index = min(
            range(len(candidate.tracks)),
            key=lambda index: track_pair_distance(local_meta, candidate.tracks[index]),
        )
    return release_to_track_edits(candidate, best_index)


async def stage_group_match(
    session: Session,
    provider_set: ProviderSet,
    group_id: int,
    source: str,
    ref_id: str,
) -> ChangeSet:
    """Re-fetches the chosen (source, ref_id) release and stages a
    match_proposal ChangeSet applying its tags to every track in the
    group, aligned via the same Hungarian solver used for scoring.

    Re-picking a candidate is just
    calling this again with a different (source, ref_id) — it always
    rebuilds the edit set from scratch from the newly-chosen release,
    never merges with a previous proposal's fields.
    """
    group = session.get(TrackGroup, group_id)
    if group is None:
        raise ValueError(f"group {group_id} not found")
    provider = provider_set.metadata.get(source)
    if provider is None:
        raise ValueError(f"provider {source!r} is not enabled")

    candidate = await provider.get_release(ProviderRef(provider=source, id=ref_id))
    if candidate is None:
        raise ValueError(f"release {ref_id!r} not found at {source!r}")

    edits = candidate_edits_for_group(group, candidate)

    return build_changeset(
        session,
        title=f"Match: {candidate.album or group.album or 'Untitled'}",
        source="match_proposal",
        edits=edits,
        entity_type="track",
        source_ref={"provider": source, "ref": ref_id},
        scope_type="group",
        scope_id=group_id,
        candidate_source=source,
        candidate_ref=ref_id,
    )


async def stage_track_match(
    session: Session,
    provider_set: ProviderSet,
    track_id: int,
    source: str,
    ref_id: str,
) -> ChangeSet:
    """Singleton equivalent of `stage_group_match` — stages a
    match_proposal ChangeSet for exactly one track, crediting it to
    the chosen release wholesale."""
    track = session.get(Track, track_id)
    if track is None:
        raise ValueError(f"track {track_id} not found")
    provider = provider_set.metadata.get(source)
    if provider is None:
        raise ValueError(f"provider {source!r} is not enabled")

    candidate = await provider.get_release(ProviderRef(provider=source, id=ref_id))
    if candidate is None:
        raise ValueError(f"release {ref_id!r} not found at {source!r}")

    edits = candidate_edits_for_track(track, candidate)
    return build_changeset(
        session,
        title=f"Match: {candidate.album or track.album or track.title or 'Untitled'}",
        source="match_proposal",
        edits={track_id: edits},
        entity_type="track",
        source_ref={"provider": source, "ref": ref_id},
        scope_type="track",
        scope_id=track_id,
        candidate_source=source,
        candidate_ref=ref_id,
    )


async def compose_track_candidate_review(
    session: Session,
    provider_set: ProviderSet,
    *,
    track_id: int,
    source: str,
    ref_id: str,
    paths_config: PathsConfig,
) -> object:
    """Compose a selected candidate into the track's active ReviewBundle.

    Kept beside the legacy staging adapter so the API never reaches ORM rows
    directly and both paths use the same provider hydration contract.
    """
    track = session.get(Track, track_id)
    if track is None:
        raise ValueError(f"track {track_id} not found")
    provider = provider_set.metadata.get(source)
    if provider is None:
        raise ValueError(f"provider {source!r} is not enabled")
    candidate = await provider.get_release(ProviderRef(provider=source, id=ref_id))
    if candidate is None:
        raise ValueError(f"release {ref_id!r} not found at {source!r}")
    # Local import avoids the proposals -> matching dependency becoming a cycle.
    from muzilla.pipeline.proposals import ProposalComposer

    return ProposalComposer(session, paths_config=paths_config).compose_candidate_for_scope(
        scope_type="track", scope_id=track_id, candidate=candidate
    )
