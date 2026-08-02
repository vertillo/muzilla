"""A tiny FastAPI app replaying committed provider fixtures, so E2E
tests exercise the real match/stage/apply pipeline against
deterministic data instead of the live network (docs/PLAN.md §11e).

MusicBrainz is enabled in ordinary E2E runs.  Deezer and Discogs routes are also
available for the URL contract journey, whose fixture opts those adapters in.

Run standalone: `python e2e/mock_provider_server.py --port 8765`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi import FastAPI, Response

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures" / "providers"

app = FastAPI()


def _load(provider: str, name: str) -> dict[str, object]:
    with (FIXTURES / provider / name).open() as f:
        result: dict[str, object] = json.load(f)
        return result


@app.get("/release")
def search_releases(query: str = "", limit: int = 5, fmt: str = "json") -> Response:
    return Response(
        content=json.dumps(_load("musicbrainz", "search_releases.json")),
        media_type="application/json",
    )


@app.get("/release/{release_id}")
def get_release(release_id: str, inc: str = "", fmt: str = "json") -> Response:
    payload = _load("musicbrainz", "get_release.json")
    return Response(content=json.dumps(payload), media_type="application/json")


@app.get("/search/album")
def search_deezer_albums(q: str = "", limit: int = 5) -> Response:
    return Response(
        content=json.dumps(_load("deezer", "search_albums.json")),
        media_type="application/json",
    )


@app.get("/album/{album_id}")
def get_deezer_album(album_id: str) -> Response:
    return Response(
        content=json.dumps(_load("deezer", "get_album.json")),
        media_type="application/json",
    )


@app.get("/track/{track_id}")
def get_deezer_track(track_id: str) -> Response:
    return Response(
        content=json.dumps(_load("deezer", "get_track.json")),
        media_type="application/json",
    )


@app.get("/database/search")
def search_discogs_releases(q: str = "", type: str = "release", per_page: int = 5) -> Response:
    return Response(
        content=json.dumps(_load("discogs", "search_release.json")),
        media_type="application/json",
    )


@app.get("/releases/{release_id}")
def get_discogs_release(release_id: str) -> Response:
    return Response(
        content=json.dumps(_load("discogs", "get_release.json")),
        media_type="application/json",
    )


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
