from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
import respx
from httpx import Response

from muzilla.config.schema import LoggingConfig
from muzilla.logging import configure_logging
from muzilla.metrics import _provider_requests, provider_request_counts
from muzilla.providers import status as provider_status
from muzilla.providers.cache import HttpClientConfig, build_http_client


async def test_build_http_client_logs_provider_response(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    """docs/product-spec.md: "provider request/response status" — a single
    httpx event hook covers every provider built via build_http_client,
    rather than a log call inside each of the six provider modules."""
    configure_logging(LoggingConfig(level="INFO", json_output=True))
    root = logging.getLogger()
    handler = root.handlers[0]
    records: list[str] = []
    original_emit = handler.emit
    handler.emit = lambda record: records.append(handler.format(record))  # type: ignore[method-assign]

    respx_mock.get("https://example.test/release/123").mock(
        return_value=Response(200, json={"ok": True})
    )

    client = build_http_client(
        HttpClientConfig(
            base_url="https://example.test",
            user_agent="muzilla-test/1.0",
            cache_dir=tmp_path / "http_cache",
        )
    )
    try:
        await client.get("/release/123")
    finally:
        await client.aclose()
        handler.emit = original_emit  # type: ignore[method-assign]

    provider_logs = [json.loads(r) for r in records if json.loads(r).get("provider_host")]
    assert len(provider_logs) == 1
    assert provider_logs[0]["provider_host"] == "example.test"
    assert provider_logs[0]["status_code"] == 200
    assert provider_logs[0]["method"] == "GET"


async def test_build_http_client_also_increments_metrics_counter(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    """docs/product-spec.md: "provider requests by source+outcome" — the
    same response hook that logs also increments muzilla.metrics'
    in-process counter, so /api/metrics stays accurate without a
    second event hook."""
    _provider_requests.clear()  # process-global counter; isolate from other tests

    respx_mock.get("https://example.test/ok").mock(return_value=Response(200))
    respx_mock.get("https://example.test/broken").mock(return_value=Response(503))

    client = build_http_client(
        HttpClientConfig(
            base_url="https://example.test",
            user_agent="muzilla-test/1.0",
            cache_dir=tmp_path / "http_cache2",
        )
    )
    try:
        await client.get("/ok")
        await client.get("/broken")
    finally:
        await client.aclose()

    counts = provider_request_counts()
    assert counts[("example.test", "success")] == 1
    assert counts[("example.test", "error")] == 1


async def test_build_http_client_records_connection_failure_as_error(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    """Regression test for §11m (docs/product-spec.md): _log_response is an
    httpx *response* event hook, so a ConnectError/ReadTimeout/DNS
    failure — which never produces an httpx.Response at all — used to
    leave muzilla_provider_requests_total completely unchanged, flat
    through a total provider outage instead of showing errors. Fixed
    via _FailureRecordingTransport, wrapped around the innermost
    transport rather than as another event hook, precisely because a
    hook can't fire for a request that never got a response."""
    _provider_requests.clear()  # process-global counter; isolate from other tests

    respx_mock.get("https://example.test/down").mock(side_effect=httpx.ConnectError("boom"))

    client = build_http_client(
        HttpClientConfig(
            base_url="https://example.test",
            user_agent="muzilla-test/1.0",
            cache_dir=tmp_path / "http_cache3",
        )
    )
    try:
        with pytest.raises(httpx.ConnectError):
            await client.get("/down")
    finally:
        await client.aclose()

    counts = provider_request_counts()
    assert counts[("example.test", "error")] == 1


async def test_connection_failure_records_provider_status_error(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    """The _FailureRecordingTransport path (connection-level failures,
    no httpx.Response) also feeds providers/status.py, not just
    muzilla.metrics — a total outage should show as a real error on the
    provider health indicator, not just leave it looking merely idle."""
    provider_status._status.clear()

    respx_mock.get("https://example.test/down").mock(side_effect=httpx.ConnectError("boom"))

    client = build_http_client(
        HttpClientConfig(
            base_url="https://example.test",
            user_agent="muzilla-test/1.0",
            cache_dir=tmp_path / "http_cache6",
            provider_name="deezer",
        )
    )
    try:
        with pytest.raises(httpx.ConnectError):
            await client.get("/down")
    finally:
        await client.aclose()
        status = provider_status.get_status("deezer")
        provider_status._status.clear()

    assert status.last_error_at is not None
    assert status.last_error_detail == "boom"
    assert status.rate_limited is False


async def test_build_http_client_records_provider_status_when_provider_name_given(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    """Provider status is fed passively from the same response hook, keyed by the logical
    provider name (not the httpx host) — only when the client was built
    with HttpClientConfig.provider_name set, as providers/set.py's real
    client construction always does."""
    provider_status._status.clear()

    respx_mock.get("https://example.test/release/123").mock(return_value=Response(200))
    respx_mock.get("https://example.test/rate-limited").mock(return_value=Response(429))

    client = build_http_client(
        HttpClientConfig(
            base_url="https://example.test",
            user_agent="muzilla-test/1.0",
            cache_dir=tmp_path / "http_cache4",
            provider_name="musicbrainz",
        )
    )
    try:
        await client.get("/release/123")
        status = provider_status.get_status("musicbrainz")
        assert status.last_success_at is not None
        assert status.rate_limited is False

        await client.get("/rate-limited")
        status = provider_status.get_status("musicbrainz")
        assert status.rate_limited is True
    finally:
        await client.aclose()
        provider_status._status.clear()


async def test_build_http_client_skips_status_recording_without_provider_name(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    """A client built without provider_name (e.g. ad-hoc test clients)
    must not silently attribute its traffic to some other provider —
    covered by the fact this doesn't raise and records nothing."""
    provider_status._status.clear()
    respx_mock.get("https://example.test/x").mock(return_value=Response(200))

    client = build_http_client(
        HttpClientConfig(
            base_url="https://example.test",
            user_agent="muzilla-test/1.0",
            cache_dir=tmp_path / "http_cache5",
        )
    )
    try:
        await client.get("/x")
    finally:
        await client.aclose()

    assert provider_status.all_statuses() == {}
