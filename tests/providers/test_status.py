from __future__ import annotations

import pytest

from muzilla.providers import status as provider_status


@pytest.fixture(autouse=True)
def _reset_status():
    provider_status._status.clear()  # process-global; isolate between tests
    yield
    provider_status._status.clear()


def test_unknown_provider_returns_all_none_status() -> None:
    status = provider_status.get_status("musicbrainz")
    assert status.last_success_at is None
    assert status.last_error_at is None
    assert status.last_error_detail is None
    assert status.rate_limited is False


def test_record_response_success_sets_last_success_at() -> None:
    provider_status.record_response("musicbrainz", 200)
    status = provider_status.get_status("musicbrainz")
    assert status.last_success_at is not None
    assert status.last_error_at is None
    assert status.rate_limited is False


def test_record_response_error_sets_last_error() -> None:
    provider_status.record_response("discogs", 503)
    status = provider_status.get_status("discogs")
    assert status.last_error_at is not None
    assert status.last_error_detail == "HTTP 503"
    assert status.rate_limited is False


def test_record_response_404_means_the_provider_is_reachable() -> None:
    provider_status.record_response("coverartarchive", 404)

    status = provider_status.get_status("coverartarchive")

    assert status.state == "operational"
    assert status.last_success_at is not None
    assert status.last_error_at is None


def test_record_response_429_marks_rate_limited() -> None:
    provider_status.record_response("musicbrainz", 429)
    status = provider_status.get_status("musicbrainz")
    assert status.rate_limited is True
    assert status.last_error_detail is not None
    assert "429" in status.last_error_detail


def test_record_response_success_after_rate_limit_clears_the_flag() -> None:
    provider_status.record_response("musicbrainz", 429)
    provider_status.record_response("musicbrainz", 200)
    status = provider_status.get_status("musicbrainz")
    assert status.rate_limited is False
    assert status.last_success_at is not None


def test_record_error_sets_last_error_detail() -> None:
    provider_status.record_error("acoustid", "connection refused")
    status = provider_status.get_status("acoustid")
    assert status.last_error_detail == "connection refused"
    assert status.last_error_at is not None
    assert status.rate_limited is False


def test_connection_check_states_are_distinct_and_keep_last_checked_time() -> None:
    provider_status.record_check_result("musicbrainz", healthy=True)
    operational = provider_status.get_status("musicbrainz")
    assert operational.state == "operational"
    assert operational.last_checked_at is not None

    provider_status.record_check_result("discogs", healthy=False, detail="HTTP 401")
    assert provider_status.get_status("discogs").state == "invalid_credentials"

    provider_status.record_check_result("deezer", healthy=False, detail="connection timed out")
    assert provider_status.get_status("deezer").state == "temporary_unavailable"


def test_stale_probe_result_cannot_overwrite_a_newer_generation() -> None:
    provider_status.record_checking("musicbrainz", generation=1)
    provider_status.record_checking("musicbrainz", generation=2)
    provider_status.record_check_result("musicbrainz", healthy=False, detail="HTTP 401", generation=2)
    provider_status.record_check_result("musicbrainz", healthy=True, generation=1)

    assert provider_status.get_status("musicbrainz").state == "invalid_credentials"


def test_all_statuses_only_includes_recorded_providers() -> None:
    provider_status.record_response("deezer", 200)
    statuses = provider_status.all_statuses()
    assert set(statuses.keys()) == {"deezer"}
