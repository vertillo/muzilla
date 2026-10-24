from __future__ import annotations

import pytest

from muzilla.db.models import SourceSnapshot
from muzilla.db.source_snapshots import SourceSnapshotItem, read_source_snapshot_items


def test_reader_returns_typed_items_from_the_orm_snapshot_payload() -> None:
    snapshot = SourceSnapshot(
        review_bundle_id=1,
        content_digest="digest",
        payload={
            "items": [
                {
                    "source_id": 7,
                    "path": "/library/song.mp3",
                    "size_bytes": 100,
                    "mtime_ns": 200,
                    "tag_hash": "tag-hash",
                },
                {"track_id": 8},
            ]
        },
    )

    assert read_source_snapshot_items(snapshot) == (
        SourceSnapshotItem(7, "/library/song.mp3", 100, 200, "tag-hash"),
        SourceSnapshotItem(8, None, None, None, None),
    )


def _physical_guard() -> dict[str, object]:
    return {
        "version": 1,
        "path": "/library/song.mp3",
        "library_root": "/library",
        "directories": [{"path": "/library", "device": 1, "inode": 2}],
        "file": {
            "device": 1,
            "inode": 3,
            "mode": 0o644,
            "size": 100,
            "mtime_ns": 200,
            "sha256": "a" * 64,
        },
    }


def _physical_guard_with_file_size(size: int) -> dict[str, object]:
    guard = _physical_guard()
    file_facts = guard["file"]
    assert isinstance(file_facts, dict)
    guard["file"] = {**file_facts, "size": size}
    return guard


def test_reader_preserves_valid_physical_guard() -> None:
    snapshot = SourceSnapshot(
        review_bundle_id=1,
        content_digest="digest",
        payload={
            "items": [
                {
                    "source_type": "track",
                    "source_id": 7,
                    "path": "/library/song.mp3",
                    "size_bytes": 100,
                    "mtime_ns": 200,
                    "tag_hash": "tag-hash",
                    "physical_guard": _physical_guard(),
                }
            ]
        },
    )

    assert read_source_snapshot_items(snapshot)[0].physical_guard == _physical_guard()


@pytest.mark.parametrize(
    "items",
    [
        [None],
        [{}],
        [{"source_id": 7, "size_bytes": "unknown"}],
        [{"source_id": True}],
        [{"source_id": 7}, {"source_id": 7}],
        [{"source_id": 7, "track_id": 8}],
        [{"source_id": 7, "source_type": "album"}],
        [
            {
                "source_id": 7,
                "path": "/library/song.mp3",
                "size_bytes": 100,
                "mtime_ns": 200,
                "physical_guard": {**_physical_guard(), "version": True},
            }
        ],
        [
            {
                "source_id": 7,
                "path": "/library/song.mp3",
                "size_bytes": 100,
                "mtime_ns": 200,
                "physical_guard": _physical_guard_with_file_size(101),
            }
        ],
    ],
)
def test_reader_rejects_malformed_snapshot_items(items: list[object]) -> None:
    snapshot = SourceSnapshot(
        review_bundle_id=1,
        content_digest="digest",
        payload={"items": items},
    )

    with pytest.raises(ValueError):
        read_source_snapshot_items(snapshot)
