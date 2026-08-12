from __future__ import annotations

import logging

from fastapi.testclient import TestClient


def test_app_startup_configures_logging(client: TestClient) -> None:
    """configure_logging is called once from
    api/app.py's lifespan -- proven by checking the root logger has a
    stream handler with our JSON formatter installed once the `client`
    fixture's app has booted. Doesn't assert an exact handler count:
    pytest's own caplog plumbing adds handlers of its own alongside it
    in this environment."""
    from muzilla.logging import _JsonFormatter

    root = logging.getLogger()
    assert any(isinstance(h.formatter, _JsonFormatter) for h in root.handlers)
