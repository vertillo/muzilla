from __future__ import annotations

from pathlib import Path

import pytest

from muzilla.services.paths_guard import require_within_library_root


def test_library_root_itself_is_allowed() -> None:
    result = require_within_library_root("/music", library_root=Path("/music"))
    assert result == Path("/music")


def test_descendant_of_library_root_is_allowed() -> None:
    result = require_within_library_root(
        "/music/artist/album", library_root=Path("/music")
    )
    assert result == Path("/music/artist/album")


def test_path_outside_library_root_is_rejected() -> None:
    with pytest.raises(ValueError, match="not the configured library root"):
        require_within_library_root("/etc", library_root=Path("/music"))


def test_traversal_out_of_library_root_is_rejected() -> None:
    with pytest.raises(ValueError, match="not the configured library root"):
        require_within_library_root("/music/../etc", library_root=Path("/music"))


def test_sibling_directory_with_shared_prefix_is_rejected() -> None:
    # /music-backup is not a descendant of /music: containment compares
    # path components rather than raw string prefixes.
    with pytest.raises(ValueError, match="not the configured library root"):
        require_within_library_root("/music-backup", library_root=Path("/music"))
