"""Path template service: the only way api/cli reach `muzilla.paths` for
rendering, previewing, and staging renames (docs/PLAN.md §6).

Owns the seam between the DB (`Track`/`TrackGroup` rows) and the pure,
network-free `paths/` engine — converts rows to variable-bindings dicts,
picks the applicable template (explicit override > query override >
album/singleton/default per the track's group kind), builds the
DB-backed `DisambiguationResolver` %aunique/%sunique need, runs batch
collision detection, and (on request) stages a `rename` ChangeSet from
the rendered results. The engine itself never touches the DB; this
module is where those two worlds meet — same shape as
`pipeline/matching.py`'s relationship to `matching/`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.config.schema import PathsConfig
from muzilla.db.models import ChangeSet, Track, TrackGroup
from muzilla.domain import fields as field_registry
from muzilla.paths.collisions import find_collisions
from muzilla.paths.context import RenderContext
from muzilla.paths.query import matches as query_matches
from muzilla.paths.query import parse_query
from muzilla.paths.render import compile_and_render, track_to_variables

_DISAMBIGUATOR_ORDER = ("year", "label", "catalog_number", "mbid_prefix")


class PathValidationError(ValueError):
    """Mirrors EditValidationError in services/edit.py — the api layer
    catches ValueError -> 400, same convention throughout services/."""


def _track_to_values(track: Track) -> dict[str, object]:
    return {
        f.name: getattr(track, f.name)
        for f in field_registry.FIELDS.values()
        if hasattr(track, f.name)
    }


def _with_extension(rendered_path: str, ext: str) -> str:
    """Appends the source file's own extension (`Track.ext`, e.g.
    ".mp3", already lowercased with its leading dot at scan time) to a
    successfully rendered path.

    The template engine (paths/render.py) deliberately has no concept
    of file extensions — it renders exactly what the template says,
    same as beets' own template language. Every example template in
    docs/PLAN.md §6 and every default in config/defaults.yaml
    ($artist - $title, etc.) omits the extension, so without this the
    rename feature silently produced extensionless, unplayable files
    on every apply — caught live by the §11e E2E rename test, not by
    any of Phase 5's own unit tests (which only ever asserted the
    rendered string against the template literally, never against a
    real file that needs to stay playable). Fixed here rather than by
    requiring every template to spell out $ext explicitly — an
    omitted extension should never be a footgun."""
    return rendered_path + ext


def _mbid_prefix(mb_release_id: str | None) -> str | None:
    if not mb_release_id:
        return None
    return mb_release_id.split("-")[0]


class DbDisambiguationResolver:
    """Concrete DisambiguationResolver (paths.context.DisambiguationResolver
    Protocol), backed by a SQLAlchemy Session. Constructed once per batch
    render call; memoizes per key internally, per docs/PLAN.md §6's
    "memoized per album per batch" requirement — a naive implementation
    would issue a DB query per track.

    Queries the *projected post-change* values (the values this same
    batch operation is about to apply), not current DB state for the
    tracks IN this batch — the "ordering trap" docs/PLAN.md §6 calls
    out: %aunique must not disambiguate against values that are about
    to change. For tracks/groups NOT in the current batch, current DB
    state is the only available signal (there's nothing else to project).
    """

    def __init__(
        self,
        session: Session,
        *,
        projected_by_key: dict[str, list[dict[str, str | None]]],
    ) -> None:
        self._session = session
        self._projected_by_key = projected_by_key
        self._cache: dict[str, str | None] = {}

    def resolve(self, key: str) -> str | None:
        if key in self._cache:
            return self._cache[key]
        candidates = self._projected_by_key.get(key, [])
        result = self._first_separating_field(candidates)
        self._cache[key] = result
        return result

    @staticmethod
    def _first_separating_field(candidates: list[dict[str, str | None]]) -> str | None:
        if len(candidates) < 2:
            return None
        for field_name in _DISAMBIGUATOR_ORDER:
            values = {c.get(field_name) for c in candidates}
            values.discard(None)
            if len(values) > 1:
                # Distinguishable by this field for at least one pair —
                # the caller-visible signal is just "use this field",
                # the actual per-item value substitution happens where
                # the resolver's result is consumed (functions.py's
                # %aunique wraps it in brackets using the field's own
                # value for the item being rendered, read directly from
                # RenderContext.values, not from this resolver).
                return field_name
        return None


def _build_group_resolver(session: Session, group_ids: set[int]) -> DbDisambiguationResolver:
    """Builds the projected-album-set keyed by (albumartist, album) —
    %aunique's default disambiguation key — covering every OTHER group
    in the library sharing that key with any group in `group_ids`, so
    a batch rename of one colliding album still disambiguates correctly
    against its (untouched) sibling."""
    projected_by_key: dict[str, list[dict[str, str | None]]] = {}
    if not group_ids:
        return DbDisambiguationResolver(session, projected_by_key={})

    target_groups = list(session.scalars(select(TrackGroup).where(TrackGroup.id.in_(group_ids))))
    keys = {
        "\x1f".join((g.album_artist or "", g.album or "")) for g in target_groups
    }

    all_candidate_groups = list(
        session.scalars(
            select(TrackGroup).where(TrackGroup.kind.in_(("album", "partial_album")))
        )
    )
    for group in all_candidate_groups:
        key = "\x1f".join((group.album_artist or "", group.album or ""))
        if key not in keys:
            continue
        projected_by_key.setdefault(key, []).append(
            {
                "year": str(group.year) if group.year is not None else None,
                "label": group.label,
                "catalog_number": group.catalog_number,
                "mbid_prefix": _mbid_prefix(group.mb_release_id),
            }
        )
    return DbDisambiguationResolver(session, projected_by_key=projected_by_key)


def _resolve_track_set(
    session: Session, *, track_ids: list[int] | None, group_id: int | None
) -> list[Track]:
    if group_id is not None:
        group = session.get(TrackGroup, group_id)
        if group is None:
            raise ValueError(f"group {group_id} not found")
        return list(group.tracks)
    if track_ids is not None:
        tracks = list(session.scalars(select(Track).where(Track.id.in_(track_ids))))
        found_ids = {t.id for t in tracks}
        missing = set(track_ids) - found_ids
        if missing:
            raise ValueError(f"track(s) not found: {sorted(missing)}")
        return tracks
    raise ValueError("must provide track_ids or group_id")


def _select_template(
    config: PathsConfig,
    *,
    values: Mapping[str, object],
    is_singleton: bool,
    template_override: str | None,
) -> str:
    if template_override is not None:
        return template_override
    for query_key, template in config.overrides.items():
        query = parse_query(query_key)
        if query_matches(query, values):
            return template
    if is_singleton:
        return config.singleton
    return config.album


@dataclass(frozen=True, slots=True)
class RenamePreviewRow:
    track_id: int
    old_path: str
    new_path: str
    errors: tuple[str, ...]
    is_collision: bool


def render_path_for_track(
    session: Session,
    track_id: int,
    *,
    config: PathsConfig,
    template_override: str | None = None,
) -> RenamePreviewRow:
    """Single-track convenience for the live-preview endpoint / CLI
    path-test — no collision check across the library (that needs a
    batch; use preview_rename for that)."""
    track = session.get(Track, track_id)
    if track is None:
        raise ValueError(f"track {track_id} not found")

    is_singleton = track.group_id is None or _group_kind(session, track.group_id) == "singleton"
    values = track_to_variables(_track_to_values(track))
    template = _select_template(
        config, values=values, is_singleton=is_singleton, template_override=template_override
    )

    resolver = None
    if track.group_id is not None:
        resolver = _build_group_resolver(session, {track.group_id})

    ctx = RenderContext(values=values, resolver=resolver)
    result = compile_and_render(
        template, ctx, create_directories=config.create_directories, replacements=tuple(config.replace)
    )
    new_path = _with_extension(result.path, track.ext) if not result.errors else result.path
    return RenamePreviewRow(
        track_id=track_id,
        old_path=track.path,
        new_path=new_path,
        errors=result.errors,
        is_collision=False,
    )


def _group_kind(session: Session, group_id: int) -> str | None:
    group = session.get(TrackGroup, group_id)
    return group.kind if group is not None else None


def _group_kinds_by_id(session: Session, group_ids: set[int]) -> dict[int, str]:
    """Batch equivalent of calling `_group_kind` once per track — a
    single query instead of one `session.get()` round-trip per track
    in a preview_rename batch, found to cost ~2.9s over 1000 tracks in
    docs/PLAN.md §11g's performance pass (the exact N+1 the plan
    predicted by inspection before this was ever measured). Chunked at
    500 ids per query: SQLite's default SQLITE_MAX_VARIABLE_NUMBER is
    999, and pipeline/grouping.py hit exactly this limit for a
    similar library-wide `IN (...)` during the same performance pass."""
    if not group_ids:
        return {}
    result: dict[int, str] = {}
    ids_list = list(group_ids)
    for i in range(0, len(ids_list), 500):
        batch = ids_list[i : i + 500]
        for g in session.scalars(select(TrackGroup).where(TrackGroup.id.in_(batch))):
            result[g.id] = g.kind
    return result


def preview_rename(
    session: Session,
    *,
    track_ids: list[int] | None = None,
    group_id: int | None = None,
    config: PathsConfig,
    template_override: str | None = None,
) -> list[RenamePreviewRow]:
    """The batch entrypoint: resolves the track set, renders every
    track (building one DisambiguationResolver over the projected
    album set spanning the whole batch), then runs collision detection
    across the batch plus every other track currently in the library,
    marking is_collision on each row."""
    tracks = _resolve_track_set(session, track_ids=track_ids, group_id=group_id)
    if not tracks:
        return []

    group_ids = {t.group_id for t in tracks if t.group_id is not None}
    resolver = _build_group_resolver(session, group_ids)
    group_kinds = _group_kinds_by_id(session, group_ids)

    rows: list[RenamePreviewRow] = []
    rendered_by_track: dict[int, str] = {}
    for track in tracks:
        is_singleton = track.group_id is None or group_kinds.get(track.group_id) == "singleton"
        values = track_to_variables(_track_to_values(track))
        template = _select_template(
            config, values=values, is_singleton=is_singleton, template_override=template_override
        )
        ctx = RenderContext(values=values, resolver=resolver)
        result = compile_and_render(
            template,
            ctx,
            create_directories=config.create_directories,
            replacements=tuple(config.replace),
        )
        new_path = _with_extension(result.path, track.ext) if not result.errors else result.path
        rows.append(
            RenamePreviewRow(
                track_id=track.id,
                old_path=track.path,
                new_path=new_path,
                errors=result.errors,
                is_collision=False,
            )
        )
        if not result.errors:
            rendered_by_track[track.id] = new_path

    batch_track_ids = {t.id for t in tracks}
    # Column-scoped select, not select(Track): loading full ORM Track
    # objects (with their JSON-column genre/artists/mood deserialization)
    # for every OTHER track in the library, just to build a path->id
    # dict, was the dominant cost of preview_rename over a 1000-track
    # batch against a 100k-track library in docs/PLAN.md §11g's
    # performance pass (~99k full-row loads for two scalar columns) —
    # a bigger cost than the _group_kind N+1 fixed alongside this.
    existing_library_paths = {
        path: track_id
        for track_id, path in session.execute(
            select(Track.id, Track.path).where(Track.id.notin_(batch_track_ids))
        )
    }
    collisions = find_collisions(
        rendered_by_track,
        create_directories=config.create_directories,
        existing_library_paths=existing_library_paths,
    )
    colliding_track_ids: set[int] = set()
    for collision in collisions:
        colliding_track_ids.update(collision.track_ids)

    return [
        RenamePreviewRow(
            track_id=row.track_id,
            old_path=row.old_path,
            new_path=row.new_path,
            errors=row.errors,
            is_collision=row.track_id in colliding_track_ids,
        )
        for row in rows
    ]


def stage_rename(
    session: Session,
    *,
    track_ids: list[int] | None = None,
    group_id: int | None = None,
    config: PathsConfig,
    template_override: str | None = None,
    created_by: str = "web",
) -> ChangeSet:
    """Refuses (PathValidationError) if any row has unresolved errors
    or an unresolved collision — "the rename job refuses to run while
    any collisions remain unresolved" (docs/PLAN.md §6). Builds a
    field='path', op='move' edit for every row whose new_path differs
    from old_path — tracks already at their correct rendered path are
    skipped entirely, never generating a pointless no-op Change."""
    rows = preview_rename(
        session,
        track_ids=track_ids,
        group_id=group_id,
        config=config,
        template_override=template_override,
    )
    if not rows:
        raise PathValidationError("no tracks to rename")

    blocking = [r for r in rows if r.errors or r.is_collision]
    if blocking:
        details = "; ".join(
            f"track {r.track_id}: "
            + (", ".join(r.errors) if r.errors else "unresolved collision")
            for r in blocking
        )
        raise PathValidationError(f"cannot stage rename — unresolved issues: {details}")

    edits: dict[int, list[FieldEdit]] = {
        row.track_id: [FieldEdit(field="path", new_value=row.new_path, op="move")]
        for row in rows
        if row.new_path != row.old_path
    }
    if not edits:
        raise PathValidationError("every track is already at its correct rendered path")

    scope_type = "group" if group_id is not None else "track"
    scope_id = group_id if group_id is not None else (track_ids[0] if track_ids and len(track_ids) == 1 else None)

    return build_changeset(
        session,
        title="Rename",
        source="rename",
        edits=edits,
        entity_type="track",
        scope_type=scope_type,
        scope_id=scope_id,
        created_by=created_by,
    )
