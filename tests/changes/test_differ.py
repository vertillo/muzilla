from __future__ import annotations

from muzilla.changes.differ import diff_field


def test_text_field_inline_diff_highlights_only_changed_span() -> None:
    d = diff_field("artist", "Beatles", "The Beatles")
    assert d.kind == "text"
    # old is entirely an insert-boundary case: nothing deleted, "The " inserted at head
    new_inserted = "".join(s.text for s in d.new_spans if s.op == "insert")
    assert new_inserted == "The "
    new_equal = "".join(s.text for s in d.new_spans if s.op == "equal")
    assert new_equal == "Beatles"


def test_text_field_diacritic_change_is_localized() -> None:
    d = diff_field("artist", "Sigur Ros", "Sigur Rós")
    old_changed = [s for s in d.old_spans if s.op != "equal"]
    new_changed = [s for s in d.new_spans if s.op != "equal"]
    # Only the "o"/"ó" differs, not the whole string
    assert "".join(s.text for s in old_changed) == "o"
    assert "".join(s.text for s in new_changed) == "ó"


def test_multi_valued_field_diffs_as_ordered_set() -> None:
    d = diff_field("genre", ("Rock", "Pop"), ("Pop", "Ambient"))
    assert d.kind == "multi_text"
    assert d.multi is not None
    assert d.multi.added == ("Ambient",)
    assert d.multi.removed == ("Rock",)
    assert d.multi.unchanged == ("Pop",)


def test_binary_field_never_carries_raw_bytes() -> None:
    d = diff_field(
        "art",
        None,
        None,
        old_binary_summary=None,
        new_binary_summary="1200x1200 image/jpeg 84.2KB",
        new_blob_id=42,
    )
    assert d.kind == "binary"
    assert d.binary is not None
    assert d.binary.new_summary == "1200x1200 image/jpeg 84.2KB"
    assert d.binary.new_blob_id == 42
    # No raw pixel data anywhere on the FieldDiff
    assert not hasattr(d, "bytes")


def test_clearing_populated_field_is_destructive() -> None:
    d = diff_field("comment", "Ripped by X", None, op="strip")
    assert d.severity == "destructive"


def test_clearing_already_empty_field_is_not_destructive() -> None:
    d = diff_field("comment", None, None, op="strip")
    assert d.severity == "normal"


def test_setting_new_value_on_empty_field_is_not_destructive() -> None:
    d = diff_field("title", None, "New Title")
    assert d.severity == "normal"


def test_scalar_field_diff() -> None:
    d = diff_field("year", 1999, 2000)
    assert d.kind == "scalar"
    assert d.old_value == 1999
    assert d.new_value == 2000


def test_unregistered_field_falls_back_to_generic_scalar_diff() -> None:
    # Grouping-correction pseudo-fields (track_ids_add, is_pinned, ...)
    # aren't in domain.fields — that registry is track-tag-scoped, not
    # group-scoped — so diff_field must degrade gracefully rather than
    # KeyError.
    d = diff_field("track_ids_add", None, [1, 2, 3])
    assert d.kind == "scalar"
    assert d.label == "Track Ids Add"
    assert d.new_value == [1, 2, 3]
