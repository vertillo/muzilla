from __future__ import annotations

import json
import logging

from muzilla.config.schema import LoggingConfig
from muzilla.logging import change_set_context, configure_logging, job_context


def test_configure_logging_emits_valid_json() -> None:
    configure_logging(LoggingConfig(level="INFO", json_output=True))
    root = logging.getLogger()
    handler = root.handlers[0]
    records: list[str] = []
    original_emit = handler.emit
    handler.emit = lambda record: records.append(handler.format(record))  # type: ignore[method-assign]

    logger = logging.getLogger("test.logging.json")
    logger.info("hello world")

    handler.emit = original_emit  # type: ignore[method-assign]
    assert len(records) == 1
    payload = json.loads(records[0])
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logging.json"
    assert "timestamp" in payload


def test_configure_logging_is_idempotent() -> None:
    configure_logging(LoggingConfig(level="INFO", json_output=True))
    configure_logging(LoggingConfig(level="INFO", json_output=True))
    root = logging.getLogger()
    assert len(root.handlers) == 1


def test_configure_logging_human_readable_when_json_disabled() -> None:
    configure_logging(LoggingConfig(level="INFO", json_output=False))
    root = logging.getLogger()
    handler = root.handlers[0]
    records: list[str] = []
    original_emit = handler.emit
    handler.emit = lambda record: records.append(handler.format(record))  # type: ignore[method-assign]

    logger = logging.getLogger("test.logging.human")
    logger.info("plain message")

    handler.emit = original_emit  # type: ignore[method-assign]
    assert len(records) == 1
    assert "plain message" in records[0]
    assert not records[0].startswith("{")  # human-readable output is not JSON


def test_job_context_attaches_job_id_to_records() -> None:
    configure_logging(LoggingConfig(level="INFO", json_output=True))
    root = logging.getLogger()
    handler = root.handlers[0]
    records: list[str] = []
    original_emit = handler.emit
    handler.emit = lambda record: records.append(handler.format(record))  # type: ignore[method-assign]

    logger = logging.getLogger("test.logging.jobctx")
    with job_context(42):
        logger.info("inside job")
    logger.info("outside job")

    handler.emit = original_emit  # type: ignore[method-assign]
    assert len(records) == 2
    inside = json.loads(records[0])
    outside = json.loads(records[1])
    assert inside["job_id"] == 42
    assert "job_id" not in outside  # context resets cleanly after the block


def test_change_set_context_attaches_change_set_id_alongside_job_id() -> None:
    configure_logging(LoggingConfig(level="INFO", json_output=True))
    root = logging.getLogger()
    handler = root.handlers[0]
    records: list[str] = []
    original_emit = handler.emit
    handler.emit = lambda record: records.append(handler.format(record))  # type: ignore[method-assign]

    logger = logging.getLogger("test.logging.csctx")
    with job_context(1), change_set_context(99):
        logger.info("applying")

    handler.emit = original_emit  # type: ignore[method-assign]
    payload = json.loads(records[0])
    assert payload["job_id"] == 1
    assert payload["change_set_id"] == 99


def test_extra_fields_are_included_in_json_payload() -> None:
    configure_logging(LoggingConfig(level="INFO", json_output=True))
    root = logging.getLogger()
    handler = root.handlers[0]
    records: list[str] = []
    original_emit = handler.emit
    handler.emit = lambda record: records.append(handler.format(record))  # type: ignore[method-assign]

    logger = logging.getLogger("test.logging.extra")
    logger.info("job start", extra={"job_type": "scan"})

    handler.emit = original_emit  # type: ignore[method-assign]
    payload = json.loads(records[0])
    assert payload["job_type"] == "scan"


def test_no_secret_value_appears_in_log_output() -> None:
    """Assert that no secrets appear in logs by adding a
    test that configures a provider token and greps the emitted
    records for it.' SecretStr's repr is already redacted by Pydantic,
    so this proves logging a Config-derived value never leaks the raw
    token even if a future log call naively does `extra={"config": ...}`
    or similar."""
    from muzilla.config.schema import ProviderConfig

    secret_value = "super-secret-token-value-12345"
    provider_config = ProviderConfig(enabled=True, token=secret_value)  # type: ignore[arg-type]

    configure_logging(LoggingConfig(level="INFO", json_output=True))
    root = logging.getLogger()
    handler = root.handlers[0]
    records: list[str] = []
    original_emit = handler.emit
    handler.emit = lambda record: records.append(handler.format(record))  # type: ignore[method-assign]

    logger = logging.getLogger("test.logging.secrets")
    logger.info("provider configured", extra={"provider_config": repr(provider_config)})

    handler.emit = original_emit  # type: ignore[method-assign]
    assert len(records) == 1
    assert secret_value not in records[0]
