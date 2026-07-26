from __future__ import annotations

from muzilla.paths.collisions import find_collisions


def test_flat_mode_two_tracks_same_path_collide() -> None:
    rendered = {1: "Artist - Title", 2: "Artist - Title"}
    collisions = find_collisions(rendered, create_directories=False)
    assert len(collisions) == 1
    assert collisions[0].path == "Artist - Title"
    assert set(collisions[0].track_ids) == {1, 2}


def test_flat_mode_no_collision() -> None:
    rendered = {1: "Artist - A", 2: "Artist - B"}
    collisions = find_collisions(rendered, create_directories=False)
    assert collisions == []


def test_flat_mode_empty_batch() -> None:
    assert find_collisions({}, create_directories=False) == []


def test_foldered_mode_same_leaf_different_directory_not_a_collision() -> None:
    rendered = {1: "AlbumA/01 Title", 2: "AlbumB/01 Title"}
    collisions = find_collisions(rendered, create_directories=True)
    assert collisions == []


def test_foldered_mode_same_directory_collision() -> None:
    rendered = {1: "AlbumA/01 Title", 2: "AlbumA/01 Title"}
    collisions = find_collisions(rendered, create_directories=True)
    assert len(collisions) == 1
    assert collisions[0].path == "AlbumA/01 Title"
    assert set(collisions[0].track_ids) == {1, 2}


def test_batch_member_colliding_with_existing_library_file() -> None:
    rendered = {1: "Artist - Title"}
    existing = {"Artist - Title": 99}
    collisions = find_collisions(rendered, create_directories=False, existing_library_paths=existing)
    assert len(collisions) == 1
    assert set(collisions[0].track_ids) == {1, 99}


def test_no_collision_when_existing_path_untouched() -> None:
    rendered = {1: "Artist - New Title"}
    existing = {"Artist - Old Title": 99}
    collisions = find_collisions(rendered, create_directories=False, existing_library_paths=existing)
    assert collisions == []


def test_existing_library_paths_none_is_safe() -> None:
    rendered = {1: "Artist - Title", 2: "Artist - Other"}
    collisions = find_collisions(rendered, create_directories=False, existing_library_paths=None)
    assert collisions == []


def test_foldered_mode_existing_library_collision_in_same_dir() -> None:
    rendered = {1: "AlbumA/01 Title"}
    existing = {"AlbumA/01 Title": 99, "AlbumB/01 Title": 50}
    collisions = find_collisions(rendered, create_directories=True, existing_library_paths=existing)
    assert len(collisions) == 1
    assert set(collisions[0].track_ids) == {1, 99}


def test_three_way_collision() -> None:
    rendered = {1: "X", 2: "X", 3: "X"}
    collisions = find_collisions(rendered, create_directories=False)
    assert len(collisions) == 1
    assert set(collisions[0].track_ids) == {1, 2, 3}


def test_track_landing_on_its_own_existing_path_is_not_a_collision() -> None:
    # Caller convention: existing_library_paths only carries OTHER
    # tracks not already a key in `rendered` -- but confirm a track_id
    # that happens to also appear as the existing entry's value isn't
    # double-counted as a self-collision if a caller passes it anyway.
    rendered = {1: "Artist - Title"}
    existing = {"Artist - Title": 1}
    collisions = find_collisions(rendered, create_directories=False, existing_library_paths=existing)
    assert collisions == []
