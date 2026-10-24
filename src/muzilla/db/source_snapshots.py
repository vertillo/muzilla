from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from muzilla.db.models import SourceSnapshot


@dataclass(frozen=True, slots=True)
class SourceSnapshotItem:
    track_id: int
    path: str | None
    size_bytes: int | None
    mtime_ns: int | None
    tag_hash: str | None
    physical_guard: dict[str, object] | None = None

    def to_manifest_item(self) -> dict[str, object]:
        return {
            "source_type": "track",
            "source_id": self.track_id,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "tag_hash": self.tag_hash,
            "physical_guard": self.physical_guard,
        }


def _valid_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _read_physical_guard(
    raw: object,
    *,
    path: str | None,
    size_bytes: int | None,
    mtime_ns: int | None,
) -> dict[str, object] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not _valid_integer(raw.get("version")) or raw["version"] != 1:
        raise ValueError("source snapshot item has invalid physical guard")
    if raw.get("path") != path or not isinstance(path, str):
        raise ValueError("source snapshot physical guard path is inconsistent")
    library_root = raw.get("library_root")
    directories = raw.get("directories")
    file_facts = raw.get("file")
    if (
        (library_root is not None and not isinstance(library_root, str))
        or not isinstance(directories, list)
        or not isinstance(file_facts, dict)
    ):
        raise ValueError("source snapshot physical guard is incomplete")
    for directory in directories:
        if (
            not isinstance(directory, dict)
            or not isinstance(directory.get("path"), str)
            or not _valid_integer(directory.get("device"))
            or not _valid_integer(directory.get("inode"))
        ):
            raise ValueError("source snapshot physical guard has invalid ancestry")
    digest = file_facts.get("sha256")
    if (
        not _valid_integer(file_facts.get("device"))
        or not _valid_integer(file_facts.get("inode"))
        or not _valid_integer(file_facts.get("mode"))
        or not _valid_integer(file_facts.get("size"))
        or not _valid_integer(file_facts.get("mtime_ns"))
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or file_facts.get("size") != size_bytes
        or file_facts.get("mtime_ns") != mtime_ns
    ):
        raise ValueError("source snapshot physical guard has invalid file facts")
    return {str(key): value for key, value in raw.items()}


def read_source_snapshot_item(raw: object) -> SourceSnapshotItem:
    """Parse one persisted source item using the shared typed schema."""
    if not isinstance(raw, dict):
        raise ValueError("source snapshot contains an invalid item")
    if raw.get("source_type", "track") != "track":
        raise ValueError("source snapshot contains an unsupported source type")
    track_id = raw.get("source_id")
    alias_id = raw.get("track_id")
    if track_id is not None and not _valid_integer(track_id):
        raise ValueError("source snapshot item has an invalid source id")
    if alias_id is not None and not _valid_integer(alias_id):
        raise ValueError("source snapshot item has an invalid track id")
    if track_id is None:
        track_id = alias_id
    elif alias_id is not None and alias_id != track_id:
        raise ValueError("source snapshot item has contradictory track ids")
    if track_id is None or isinstance(track_id, bool) or not isinstance(track_id, int):
        raise ValueError("source snapshot item has no track id")
    if track_id <= 0:
        raise ValueError("source snapshot item has no track id")
    path = raw.get("path")
    size_bytes = raw.get("size_bytes")
    mtime_ns = raw.get("mtime_ns")
    tag_hash = raw.get("tag_hash")
    if (
        (path is not None and (not isinstance(path, str) or not path))
        or (size_bytes is not None and (not _valid_integer(size_bytes) or size_bytes < 0))
        or (mtime_ns is not None and not _valid_integer(mtime_ns))
        or (tag_hash is not None and (not isinstance(tag_hash, str) or not tag_hash))
    ):
        raise ValueError("source snapshot item has invalid source facts")
    physical_guard = _read_physical_guard(
        raw.get("physical_guard"), path=path, size_bytes=size_bytes, mtime_ns=mtime_ns
    )
    return SourceSnapshotItem(
        track_id=track_id,
        path=path,
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        tag_hash=tag_hash,
        physical_guard=physical_guard,
    )


def read_source_snapshot_items(snapshot: SourceSnapshot) -> tuple[SourceSnapshotItem, ...]:
    """Read the persisted item payload without treating its ORM relation as a dict."""
    payload = snapshot.payload
    if not isinstance(payload, dict):
        raise ValueError("source snapshot payload is invalid")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("source snapshot has no item list")

    items: list[SourceSnapshotItem] = []
    seen_ids: set[int] = set()
    for raw in raw_items:
        item = read_source_snapshot_item(raw)
        if item.track_id in seen_ids:
            raise ValueError("source snapshot contains duplicate track ids")
        seen_ids.add(item.track_id)
        items.append(item)
    return tuple(items)


def read_source_snapshot_track_ids(
    snapshot: SourceSnapshot, required_track_ids: Iterable[int]
) -> set[int]:
    """Read snapshot identities and reject missing review-target coverage."""
    track_ids = {item.track_id for item in read_source_snapshot_items(snapshot)}
    if missing := set(required_track_ids) - track_ids:
        raise ValueError(f"source snapshot omits review targets: {sorted(missing)}")
    return track_ids
