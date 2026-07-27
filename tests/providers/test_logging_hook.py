from __future__ import annotations

import json
import logging
from pathlib import Path

import respx
from httpx import Response

from muzilla.config.schema import LoggingConfig
from muzilla.logging import configure_logging
from muzilla.providers.cache import HttpClientConfig, build_http_client


async def test_build_http_client_logs_provider_response(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    """docs/PLAN.md §11d: "provider request/response status" — a single
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
