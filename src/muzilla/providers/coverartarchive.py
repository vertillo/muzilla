"""Cover Art Archive `ArtProvider` client.

CAA has no search — it's purely art-by-MusicBrainz-release-id, so it
implements only `ArtProvider`, not `MetadataProvider`. Its list
endpoint gives URLs and declared `types`/`approved` flags but no real
pixel dimensions, so `ArtRef.width`/`height`/`mime` are left None
rather than guessed.
"""

from __future__ import annotations

from typing import Any

import httpx

from muzilla.providers.base import ArtRef, Capability, ProviderHealth, ProviderRef
from muzilla.providers.ratelimit import get_limiter

_CAPABILITIES = frozenset({Capability.ART})


class CoverArtArchiveProvider:
    name = "coverartarchive"
    capabilities = _CAPABILITIES
    requires_auth = False

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_art(self, ref: ProviderRef) -> list[ArtRef]:
        try:
            async with get_limiter(self.name):
                response = await self._client.get(f"/release/{ref.id}")
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise
        payload = response.json()
        return [self._art_ref_from_image(image) for image in payload.get("images", [])]

    def _art_ref_from_image(self, image: dict[str, Any]) -> ArtRef:
        return ArtRef(url=image["image"], source=self.name)

    async def health(self) -> ProviderHealth:
        try:
            async with get_limiter(self.name):
                response = await self._client.head("/")
                if response.status_code >= 500:
                    response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            return ProviderHealth(name=self.name, healthy=False, detail=str(exc))
        return ProviderHealth(name=self.name, healthy=True)


__all__ = ["CoverArtArchiveProvider"]
