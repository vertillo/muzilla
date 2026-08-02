"""Pure allow-listed recognition of public provider candidate URLs.

The registry never performs I/O and never returns a URL.  Its only output is a
provider/type/identifier tuple that application services may pass to an already
configured provider adapter.  Keeping this boundary pure makes it impossible for an
input host, port, path, query or redirect target to become a server-side fetch target.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

CandidateUrlType = Literal["release", "album", "track"]

_MAX_URL_LENGTH = 2048
_MUSICBRAINZ_HOSTS = frozenset({"musicbrainz.org", "www.musicbrainz.org"})
_DEEZER_HOSTS = frozenset({"deezer.com", "www.deezer.com"})
_DISCOGS_HOSTS = frozenset({"discogs.com", "www.discogs.com"})
_ALL_HOSTS = _MUSICBRAINZ_HOSTS | _DEEZER_HOSTS | _DISCOGS_HOSTS

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_MUSICBRAINZ_RELEASE = re.compile(rf"^/release/(?P<id>{_UUID})/?$")
_DEEZER_CANDIDATE = re.compile(
    r"^/(?:[a-zA-Z]{2}/)?(?P<type>album|track)/(?P<id>[0-9]{1,20})/?$"
)
_DISCOGS_RELEASE = re.compile(
    r"^/release/(?P<id>[0-9]{1,20})(?:-[^/]*)?/?$",
    flags=re.IGNORECASE,
)


class CandidateUrlError(ValueError):
    """Base class for safe, user-displayable recognition failures."""


class MalformedCandidateUrl(CandidateUrlError):
    pass


class UnsupportedCandidateUrl(CandidateUrlError):
    def __init__(self, provider: str, candidate_type: str) -> None:
        self.provider = provider
        self.candidate_type = candidate_type
        super().__init__(
            f"{provider} {candidate_type!r} URLs are not supported for candidates"
        )


@dataclass(frozen=True, slots=True)
class CandidateUrlRef:
    provider: Literal["musicbrainz", "deezer", "discogs"]
    candidate_type: CandidateUrlType
    provider_id: str


def _unsupported_type(path: str) -> str:
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) >= 2 and len(segments[0]) == 2:
        return segments[1].lower()
    if segments:
        return segments[0].lower()
    return "unknown"


def recognize_candidate_url(value: str) -> CandidateUrlRef:
    """Recognize an exact public provider URL without contacting any host."""
    raw = value.strip()
    if (
        not raw
        or len(raw) > _MAX_URL_LENGTH
        or raw != value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw)
    ):
        raise MalformedCandidateUrl("candidate URL must be a bounded absolute HTTPS URL")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise MalformedCandidateUrl("candidate URL authority is malformed") from exc

    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise MalformedCandidateUrl(
            "candidate URL must use HTTPS without credentials, port, query, or fragment"
        )
    hostname = parsed.hostname
    if hostname not in _ALL_HOSTS:
        raise MalformedCandidateUrl("candidate URL host is not allow-listed")

    lowered_path = parsed.path.lower()
    if "\\" in parsed.path or "%2f" in lowered_path or "%5c" in lowered_path:
        raise MalformedCandidateUrl("candidate URL path is ambiguous")

    if hostname in _MUSICBRAINZ_HOSTS:
        match = _MUSICBRAINZ_RELEASE.fullmatch(parsed.path)
        if match is None:
            raise UnsupportedCandidateUrl("musicbrainz", _unsupported_type(parsed.path))
        return CandidateUrlRef(
            provider="musicbrainz",
            candidate_type="release",
            provider_id=match.group("id").lower(),
        )

    if hostname in _DEEZER_HOSTS:
        match = _DEEZER_CANDIDATE.fullmatch(parsed.path)
        if match is None:
            raise UnsupportedCandidateUrl("deezer", _unsupported_type(parsed.path))
        candidate_type: CandidateUrlType = (
            "album" if match.group("type").lower() == "album" else "track"
        )
        return CandidateUrlRef(
            provider="deezer",
            candidate_type=candidate_type,
            provider_id=match.group("id"),
        )

    match = _DISCOGS_RELEASE.fullmatch(parsed.path)
    if match is None:
        raise UnsupportedCandidateUrl("discogs", _unsupported_type(parsed.path))
    return CandidateUrlRef(
        provider="discogs",
        candidate_type="release",
        provider_id=match.group("id"),
    )


__all__ = [
    "CandidateUrlError",
    "CandidateUrlRef",
    "CandidateUrlType",
    "MalformedCandidateUrl",
    "UnsupportedCandidateUrl",
    "recognize_candidate_url",
]
