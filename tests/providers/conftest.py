"""Provider test isolation (docs/PLAN.md's testing strategy tier 1/2:
unit + transport tests must never hit the network).

Scoped to `tests/providers/` only (not the repo-wide `tests/conftest.py`)
so it can't interfere with `tests/api/`'s `TestClient`-based tests,
which exercise the ASGI app in-process and never go over real HTTP.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx


@pytest.fixture(autouse=True)
def respx_mock() -> Iterator[respx.MockRouter]:
    """Any httpx call not explicitly mocked by a test raises, rather
    than silently attempting a real network request. Autouse + yielded
    so every test in this directory gets network isolation for free,
    while still being able to register routes via the yielded router."""
    with respx.mock(assert_all_called=False) as router:
        yield router
