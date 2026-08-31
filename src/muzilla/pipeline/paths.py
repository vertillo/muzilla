"""Path template composition for database-backed proposal flows.

Owns the seam between the DB (`Track`/`TrackGroup` rows) and the pure,
network-free `paths/` engine — converts rows to variable-bindings dicts,
picks the applicable template, and runs batch collision detection.
Rename staging is now via ReviewBundle operations in ``pipeline/proposals.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.config.schema import PathsConfig
from muzilla.db.batching import batched
from muzilla.db.models import Track
from muzilla.domain import fields as field_registry
from muzilla.paths.collisions import find_collisions
from muzilla.paths.context import RenderContext
from muzilla.paths.query import matches as query_matches
from muzilla.paths.query import parse_query
from muzilla.paths.render import compile_and_render, track_to_variables


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
    successfully rendered path."""
    return rendered_path + ext


def _clamp_filename_to_255(path: str, ext: str) -> str:
    """Ensure final path component plus extension fits in 255 bytes.

    sanitize_component clamps each component to 255 before extension is
    appended; appending the extension can push the final filename over
    the limit. Truncate the final component's base (preserving ext) to
    255 bytes without splitting UTF-8 or leaving a dangling combining
    mark, so identical long titles still collide rather than error."""
    if "/" in path:
        prefix, filename = path.rsplit("/", 1)
        prefix += "/"
    else:
        prefix, filename = "", path
    if len(filename.encode("utf-8")) <= 255:
        return path
    ext_bytes_len = len(ext.encode("utf-8"))
    max_base_bytes = 255 - ext_bytes_len
    if max_base_bytes <= 0:
        # ext itself too long - fallback to clamped filename
        from muzilla.paths.sanitize import _clamp_bytes

        return prefix + _clamp_bytes(filename, 255)
    # filename is base+ext; strip ext to clamp base
    base = (filename[: -len(ext)] if ext else filename) if filename.endswith(ext) else filename
    from muzilla.paths.sanitize import _clamp_bytes

    clamped_base = _clamp_bytes(base, max_base_bytes)
    return prefix + clamped_base + ext


def _resolve_track_set(
    session: Session, *, track_ids: list[int] | None, group_id: int | None
) -> list[Track]:
    from muzilla.db.models import TrackGroup

    if group_id is not None:
        group = session.get(TrackGroup, group_id)
        if group is None:
            raise ValueError(f"group {group_id} not found")
        return list(group.tracks)
    if track_ids is not None:
        tracks: list[Track] = []
        for batch in batched(track_ids):
            tracks.extend(session.scalars(select(Track).where(Track.id.in_(batch))))
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
    conflicting_track_ids: tuple[int, ...] = ()
    collision_path: str | None = None


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

    ctx = RenderContext(values=values)
    result = compile_and_render(
        template,
        ctx,
        create_directories=config.create_directories,
        replacements=tuple(config.replace),
    )
    new_path = _with_extension(result.path, track.ext) if not result.errors else result.path
    extra_errors: list[str] = list(result.errors)
    new_path_final = new_path
    if not result.errors:
        final_component = new_path.rsplit("/", 1)[-1]
        if not final_component.strip() or any(not c.strip() for c in new_path.split("/")):
            extra_errors.append(f"template rendered an empty path component: {new_path!r}")
            new_path_final = result.path
        elif len(final_component.encode("utf-8")) > 255:
            new_path_final = _clamp_filename_to_255(new_path, track.ext)
        # clamp never introduces an error; identical long titles still collide
    errors_tuple = tuple(extra_errors)
    return RenamePreviewRow(
        track_id=track_id,
        old_path=track.path,
        new_path=new_path_final,
        errors=errors_tuple,
        is_collision=False,
    )


def _group_kind(session: Session, group_id: int) -> str | None:
    from muzilla.db.models import TrackGroup

    group = session.get(TrackGroup, group_id)
    return group.kind if group is not None else None


def _group_kinds_by_id(session: Session, group_ids: set[int]) -> dict[int, str]:
    """Load group kinds in bounded batches for a rename preview."""
    from muzilla.db.models import TrackGroup

    if not group_ids:
        return {}
    result: dict[int, str] = {}
    for batch in batched(group_ids):
        for g in session.scalars(select(TrackGroup).where(TrackGroup.id.in_(batch))):
            result[g.id] = g.kind
    return result


def _relative_to_library(path_str: str, library_root: Path | None) -> str:
    """Convert a Track.path to a library-root-relative path lexically.

    Real scans store absolute NFC paths; rendered destinations are relative.
    This helper is intentionally pure and performs no filesystem I/O
    (no ``Path.resolve`` / ``exists``) so preview remains non-mutating.
    When library_root is known, derive the relative path by lexical
    prefix stripping; otherwise fall back to stripping a leading slash.
    The returned string is NOT normalized here — normalization (NFC+casefold)
    is handled by ``find_collisions``.
    """
    if library_root is not None:
        try:
            # Lexical, no I/O: compare normalized string prefixes.
            root_str = str(library_root).rstrip("/")
            if not root_str:
                return path_str.lstrip("/")
            # Normalize path_str lexically without touching the filesystem.
            # Preserve the original string for relative paths; only strip prefix for
            # absolute paths that lexically lie under library_root.
            if path_str == root_str:
                return ""
            prefix = root_str + "/"
            if path_str.startswith(prefix):
                return path_str[len(prefix) :]
            # Also handle case where path_str is already relative but library_root is
            # absolute — fall through to lstrip. Pure lexical, no resolve().
        except Exception:
            pass
    return path_str.lstrip("/")


def preview_rename(
    session: Session,
    *,
    track_ids: list[int] | None = None,
    group_id: int | None = None,
    config: PathsConfig,
    template_override: str | None = None,
    proposed_values_by_track_id: Mapping[int, Mapping[str, object]] | None = None,
    library_root: Path | None = None,
) -> list[RenamePreviewRow]:
    """The batch entrypoint: resolves the track set, renders every
    track, then runs collision detection across the batch plus every
    other track currently in the library, marking is_collision on each row."""
    # Resolve library_root from config if not explicitly passed
    if library_root is None:
        try:
            from muzilla.config.loader import load_config

            library_root = Path(load_config().storage.library_root)
        except Exception:
            library_root = None

    tracks = _resolve_track_set(session, track_ids=track_ids, group_id=group_id)
    if not tracks:
        return []

    group_ids = {t.group_id for t in tracks if t.group_id is not None}
    group_kinds = _group_kinds_by_id(session, group_ids)

    rows: list[RenamePreviewRow] = []
    rendered_by_track: dict[int, str] = {}
    for track in tracks:
        is_singleton = track.group_id is None or group_kinds.get(track.group_id) == "singleton"
        proposed = (proposed_values_by_track_id or {}).get(track.id, {})
        values: dict[str, object] = _track_to_values(track)
        values.update(proposed)
        rendered_values = track_to_variables(values)
        template = _select_template(
            config,
            values=rendered_values,
            is_singleton=is_singleton,
            template_override=template_override,
        )
        ctx = RenderContext(values=rendered_values)
        result = compile_and_render(
            template,
            ctx,
            create_directories=config.create_directories,
            replacements=tuple(config.replace),
        )
        new_path = _with_extension(result.path, track.ext) if not result.errors else result.path
        extra_errors: list[str] = list(result.errors)
        final_new_path = new_path
        if not result.errors:
            final_component = new_path.rsplit("/", 1)[-1]
            if not final_component.strip() or any(not c.strip() for c in new_path.split("/")):
                extra_errors.append(f"template rendered an empty path component: {new_path!r}")
                final_new_path = result.path
            elif len(final_component.encode("utf-8")) > 255:
                final_new_path = _clamp_filename_to_255(new_path, track.ext)
        errors_tuple = tuple(extra_errors)
        rows.append(
            RenamePreviewRow(
                track_id=track.id,
                old_path=track.path,
                new_path=final_new_path,
                errors=errors_tuple,
                is_collision=False,
            )
        )
        if not errors_tuple:
            rendered_by_track[track.id] = final_new_path

    batch_track_ids = {t.id for t in tracks}
    existing_library_paths = {
        _relative_to_library(path, library_root): track_id
        for track_id, path in session.execute(select(Track.id, Track.path))
        if track_id not in batch_track_ids
    }
    collisions = find_collisions(
        rendered_by_track,
        create_directories=config.create_directories,
        existing_library_paths=existing_library_paths,
        library_root=library_root,
    )
    colliding_map: dict[int, tuple[int, ...]] = {}
    collision_path_by_track: dict[int, str] = {}
    for collision in collisions:
        for tid in collision.track_ids:
            others = tuple(x for x in collision.track_ids if x != tid)
            colliding_map[tid] = others
            collision_path_by_track[tid] = collision.path

    return [
        RenamePreviewRow(
            track_id=row.track_id,
            old_path=row.old_path,
            new_path=row.new_path,
            errors=row.errors,
            is_collision=row.track_id in colliding_map,
            conflicting_track_ids=colliding_map.get(row.track_id, ()),
            collision_path=collision_path_by_track.get(row.track_id),
        )
        for row in rows
    ]


def stage_rename(*_args: object, **_kwargs: object) -> None:
    """Legacy ChangeSet entry point removed; use ReviewBundle move proposals."""
    raise NotImplementedError("rename ChangeSet staging removed: use ReviewBundle")
