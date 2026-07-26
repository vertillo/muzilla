from __future__ import annotations

import unicodedata

from muzilla.paths.sanitize import sanitize_component


def test_reserved_chars_replaced() -> None:
    for ch in '<>:"/\\|?*':
        assert sanitize_component(f"a{ch}b") == "a_b"


def test_control_chars_replaced() -> None:
    assert sanitize_component("a\x01b") == "a_b"


def test_custom_substitute_char() -> None:
    assert sanitize_component("AC/DC", substitute="-") == "AC-DC"


def test_trailing_dot_stripped() -> None:
    assert sanitize_component("file.") == "file"


def test_trailing_space_stripped() -> None:
    assert sanitize_component("file ") == "file"


def test_trailing_dot_then_space_stripped() -> None:
    assert sanitize_component("file. ") == "file"
    assert sanitize_component("file .") == "file"


def test_leading_dot_preserved() -> None:
    assert sanitize_component(".hidden") == ".hidden"


def test_windows_reserved_name_case_insensitive() -> None:
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("con") == "_con"
    assert sanitize_component("Con") == "_Con"


def test_windows_reserved_name_with_extension() -> None:
    assert sanitize_component("NUL.txt") == "_NUL.txt"


def test_windows_reserved_com_and_lpt_ports() -> None:
    assert sanitize_component("COM1") == "_COM1"
    assert sanitize_component("LPT9") == "_LPT9"


def test_non_reserved_name_untouched() -> None:
    assert sanitize_component("CONcert") == "CONcert"


def test_nfc_normalization() -> None:
    decomposed = unicodedata.normalize("NFD", "é")  # e + combining acute
    result = sanitize_component(decomposed)
    assert result == unicodedata.normalize("NFC", "é")
    assert len(result) == 1


def test_max_bytes_clamping_ascii() -> None:
    long_name = "a" * 300
    result = sanitize_component(long_name, max_bytes=255)
    assert len(result.encode("utf-8")) <= 255
    assert len(result) == 255


def test_max_bytes_clamping_does_not_split_combining_pair() -> None:
    # A run of "e" + combining acute accent pairs, long enough to force
    # a clamp right at/near a base+combining boundary.
    unit = "é"  # 3 bytes: 'e' (1) + U+0301 (2, UTF-8)
    text = unit * 100  # 300 bytes
    result = sanitize_component(text, max_bytes=100)
    assert len(result.encode("utf-8")) <= 100
    # The result must not end with a stray combining mark whose base
    # character got cut off.
    assert not unicodedata.combining(result[-1])


def test_configurable_replace_list_applied_before_char_replacement() -> None:
    result = sanitize_component("hello world", replacements=[(r"o", "0")])
    assert result == "hell0 w0rld"


def test_replace_list_runs_before_reserved_char_substitution() -> None:
    # A replace rule that introduces a reserved char should still get
    # that char substituted afterward.
    result = sanitize_component("safe", replacements=[(r"afe", "a/e")])
    assert result == "sa_e"


def test_empty_component() -> None:
    assert sanitize_component("") == ""
