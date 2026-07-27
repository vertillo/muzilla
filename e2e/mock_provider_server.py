"""A tiny FastAPI app replaying committed provider fixtures, so E2E
tests exercise the real match/stage/apply pipeline against
deterministic data instead of the live network (docs/PLAN.md §11e).

Only MusicBrainz is served — the other providers are disabled via
config for E2E runs. Matching's "one release, one source" model
(docs/PLAN.md §3) means a single-provider candidate list is a fully
valid path through the pipeline, not a shortcut around it; the two
E2E tests exercise apply/undo and the rename flow, neither of which
depends on multi-source ranking.

Run standalone: `python e2e/mock_provider_server.py --port 8765`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi import FastAPI, Response

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures" / "providers" / "musicbrainz"

app = FastAPI()


def _load(name: str) -> dict[str, object]:
    with (FIXTURES / name).open() as f:
        result: dict[str, object] = json.load(f)
        return result


@app.get("/release")
def search_releases(query: str = "", limit: int = 5, fmt: str = "json") -> Response:
    return Response(
        content=json.dumps(_load("search_releases.json")), media_type="application/json"
    )


@app.get("/release/{release_id}")
def get_release(release_id: str, inc: str = "", fmt: str = "json") -> Response:
    payload = _load("get_release.json")
    return Response(content=json.dumps(payload), media_type="application/json")


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
