"""A tiny FastAPI app replaying committed provider fixtures, so E2E
tests exercise the real match/stage/apply pipeline against
deterministic data instead of the live network (docs/product-spec.md).

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
E2E_RELEASE_ID = "11111111-2222-3333-4444-555555555555"


def _load(provider: str, name: str) -> dict[str, object]:
    with (FIXTURES / provider / name).open() as f:
        result: dict[str, object] = json.load(f)
        return result


@app.get("/release")
def search_releases(query: str = "", limit: int = 5, fmt: str = "json") -> Response:
    payload = _load("musicbrainz", "search_releases.json")
    releases = payload.get("releases")
    assert isinstance(releases, list) and isinstance(releases[0], dict)
    if "e2e" in query.casefold():
        matching_release = json.loads(json.dumps(releases[0]))
        matching_release.update(
            {
                "id": E2E_RELEASE_ID,
                "title": "E2E Album",
                "date": "2026-01-01",
                "track-count": 1,
                "artist-credit": [
                    {
                        "name": "E2E Artist",
                        "artist": {"id": "e2e-artist", "name": "E2E Artist"},
                    }
                ],
            }
        )
        releases.insert(0, matching_release)
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
    )


@app.get("/release/{release_id}")
def get_release(release_id: str, inc: str = "", fmt: str = "json") -> Response:
    payload = _load("musicbrainz", "get_release.json")
    if release_id == E2E_RELEASE_ID:
        payload.update(
            {
                "id": E2E_RELEASE_ID,
                "title": "E2E Album",
                "date": "2026-01-01",
                "artist-credit": [
                    {
                        "name": "E2E Artist",
                        "artist": {"id": "e2e-artist", "name": "E2E Artist"},
                    }
                ],
                "media": [
                    {
                        "position": 1,
                        "format": "Digital Media",
                        "tracks": [
                            {
                                "id": "e2e-track",
                                "position": 1,
                                "number": "1",
                                "title": "E2E Track",
                                "length": 1045,
                                "artist-credit": [
                                    {
                                        "name": "E2E Artist",
                                        "artist": {
                                            "id": "e2e-artist",
                                            "name": "E2E Artist",
                                        },
                                    }
                                ],
                                "recording": {
                                    "id": "e2e-recording",
                                    "length": 1045,
                                    "isrcs": [],
                                },
                            }
                        ],
                    }
                ],
            }
        )
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
