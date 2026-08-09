from __future__ import annotations

from fastapi.testclient import TestClient


def test_legacy_groups_crud_and_reassign_are_not_public(client: TestClient) -> None:
    """Grouping remains internal; the only user entry is a constrained review."""
    for method, path in (
        ("get", "/api/groups"),
        ("get", "/api/groups/1"),
        ("post", "/api/groups/cascade"),
        ("post", "/api/groups/1/merge"),
        ("post", "/api/groups/1/split"),
        ("post", "/api/groups/1/pin"),
        ("post", "/api/groups/force-singleton"),
        ("post", "/api/groups/1/reassign"),
        ("get", "/api/groups/1/candidates"),
        ("post", "/api/groups/1/stage"),
    ):
        response = getattr(client, method)(path)
        # GET routes resolve to the API 404 guard; non-GET methods may be
        # rejected by FastAPI's GET-only SPA fallback before it is invoked.
        assert response.status_code in {404, 405}, path
