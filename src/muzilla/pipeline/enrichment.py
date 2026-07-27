"""Enrichment orchestration: ReplayGain, album art, and lyrics
(docs/PLAN.md §Phase-6 — "complete, not just correct" metadata).

Same seam as `pipeline/matching.py`: pure computation (`audio/`,
`providers/`) meets the DB here, and the result is staged as an
ordinary `enrichment` ChangeSet via `changes/builder.py` — enrichment
never writes a file directly, exactly like every other mutation path
(CLAUDE.md: "nothing touches disk until a ChangeSet is applied").

Lives in `muzilla.pipeline` (not `services`) so `jobs/handlers/` can
call it directly without a layering violation — see pipeline/matching.py's
docstring for the same reasoning. `services.enrichment` re-exports this
module's public names for api/cli.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.audio.replaygain import compute_album_replaygain
from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import ChangeSet, Track, TrackGroup


def groups_needing_replaygain(session: Session) -> list[TrackGroup]:
    """Groups (album OR singleton — §7b treats them as equal peers)
    containing at least one track with no track-gain value yet. Scoped
    by group, not by track directly, because album gain is computed
    for a whole group's files together in one `rsgain` invocation —
    see `stage_replaygain_for_group`. `r128_track_gain` isn't checked:
    rsgain's `-O tab` mode always reports the EBU R128-based
    `Gain`/`Peak` pair together, so `rg_track_gain` being set is
    sufficient evidence a track was already analyzed."""
    group_ids = session.scalars(
        select(Track.group_id)
        .where(Track.missing_since.is_(None), Track.rg_track_gain.is_(None))
        .where(Track.group_id.is_not(None))
        .distinct()
    )
    ids = list(group_ids)
    if not ids:
        return []
    return list(session.scalars(select(TrackGroup).where(TrackGroup.id.in_(ids))))


def stage_replaygain_for_group(
    session: Session, group: TrackGroup, *, force: bool = False
) -> ChangeSet | None:
    """Analyzes every track in `group` together (album gain isn't
    decomposable per-file) and stages one `enrichment` ChangeSet
    covering all of them. Returns None if every track already has a
    value and `force` is False, or if rsgain produced no usable result
    at all (e.g. every file in the group is corrupt)."""
    tracks = [t for t in group.tracks if t.missing_since is None]
    if not force:
        tracks = [t for t in tracks if t.rg_track_gain is None]
    if not tracks:
        return None

    results = compute_album_replaygain([Path(t.path) for t in tracks])
    edits: dict[int, list[FieldEdit]] = {}
    for track in tracks:
        rg = results.get(Path(track.path))
        if rg is None:
            continue
        field_edits = [
            FieldEdit("rg_track_gain", rg.track_gain_db, op="set"),
            FieldEdit("rg_track_peak", rg.track_peak, op="set"),
        ]
        if rg.album_gain_db is not None:
            field_edits.append(FieldEdit("rg_album_gain", rg.album_gain_db, op="set"))
        if rg.album_peak is not None:
            field_edits.append(FieldEdit("rg_album_peak", rg.album_peak, op="set"))
        edits[track.id] = field_edits

    if not edits:
        return None

    return build_changeset(
        session,
        title=f"ReplayGain: {group.album or 'Untitled'}",
        source="enrichment",
        edits=edits,
        entity_type="track",
        source_ref={"kind": "replaygain"},
        scope_type="group",
        scope_id=group.id,
        created_by="job",
    )
