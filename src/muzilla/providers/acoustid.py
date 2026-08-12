"""AcoustID fingerprint lookup (docs/product-spec.md AcoustID short-circuit).

Deliberately does NOT reuse `pyacoustid`'s built-in `lookup()` — that
function uses sync `requests`, bypassing this project's rate limiter
and httpx cache entirely. This module talks to the same
`https://api.acoustid.org/v2/lookup` endpoint directly over the shared
`httpx.AsyncClient`, wrapped in `get_limiter("acoustid")` like every
other provider call.
"""

from __future__ import annotations

import httpx

from muzilla.providers.base import (
    Capability,
    FingerprintMatch,
    ProviderHealth,
)
from muzilla.providers.ratelimit import get_limiter


class AcoustIDProvider:
    name = "acoustid"
    capabilities = frozenset({Capability.FINGERPRINT_LOOKUP})
    requires_auth = True

    def __init__(self, client: httpx.AsyncClient, api_key: str | None) -> None:
        self._client = client
        self._api_key = api_key

    async def lookup(self, fingerprint: str, duration_s: float) -> list[FingerprintMatch]:
        if not self._api_key:
            raise RuntimeError("AcoustID requires a free API key; none is configured")

        params = {
            "client": self._api_key,
            "format": "json",
            "duration": str(int(duration_s)),
            "fingerprint": fingerprint,
            "meta": "recordings+releaseids",
        }
        try:
            async with get_limiter("acoustid"):
                response = await self._client.get("/lookup", params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError:
            return []

        data = response.json()
        if data.get("status") != "ok":
            return []

        matches: list[FingerprintMatch] = []
        for result in data.get("results", []):
            score = float(result.get("score", 0.0))
            for recording in result.get("recordings", []):
                recording_id = recording.get("id")
                if not recording_id:
                    continue
                release_ids = tuple(
                    r["id"] for r in recording.get("releases", []) if r.get("id")
                )
                matches.append(
                    FingerprintMatch(
                        mb_recording_id=recording_id,
                        mb_release_ids=release_ids,
                        score=score,
                    )
                )
        return matches

    async def health(self) -> ProviderHealth:
        if not self._api_key:
            return ProviderHealth(
                name=self.name, healthy=False, detail="no AcoustID API key configured"
            )
        # AcoustID has no separate authenticated health endpoint.  A small,
        # syntactically valid Chromaprint lookup exercises the configured
        # credential without depending on a match being present.
        try:
            async with get_limiter("acoustid"):
                response = await self._client.get(
                    "/lookup",
                    params={
                        "client": self._api_key,
                        "format": "json",
                        "duration": "1",
                        "fingerprint": "AQAAO0mUaEkSZSoA",
                    },
                )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            return ProviderHealth(name=self.name, healthy=False, detail=f"HTTP {exc.response.status_code}")
        except (httpx.HTTPError, ValueError):
            return ProviderHealth(name=self.name, healthy=False, detail="connection check failed")
        if data.get("status") != "ok":
            return ProviderHealth(name=self.name, healthy=False, detail="invalid credentials")
        return ProviderHealth(name=self.name, healthy=True)
