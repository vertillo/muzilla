from __future__ import annotations

import pytest

from muzilla.providers.url_registry import (
    MalformedCandidateUrl,
    UnsupportedCandidateUrl,
    recognize_candidate_url,
)


@pytest.mark.parametrize(
    ("url", "provider", "candidate_type", "provider_id"),
    [
        (
            "https://musicbrainz.org/release/076AD60E-2B19-31B0-9C02-A5A2F0A4B1C8",
            "musicbrainz",
            "release",
            "076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8",
        ),
        ("https://www.deezer.com/album/302127", "deezer", "album", "302127"),
        ("https://www.deezer.com/it/track/3135556", "deezer", "track", "3135556"),
        (
            "https://www.discogs.com/release/439334-Sigur-R%C3%B3s-%C3%81g%C3%A6tis-Byrjun",
            "discogs",
            "release",
            "439334",
        ),
    ],
)
def test_recognizes_allowlisted_candidate_urls(
    url: str, provider: str, candidate_type: str, provider_id: str
) -> None:
    recognized = recognize_candidate_url(url)

    assert recognized.provider == provider
    assert recognized.candidate_type == candidate_type
    assert recognized.provider_id == provider_id


@pytest.mark.parametrize(
    "url",
    [
        "http://musicbrainz.org/release/076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8",
        "file:///etc/passwd",
        "https://127.0.0.1/release/123",
        "https://[::1]/release/123",
        "https://localhost/release/123",
        "https://musicbrainz.org.evil.example/release/076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8",
        "https://api.musicbrainz.org/release/076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8",
        "https://musicbrainz.org@127.0.0.1/release/076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8",
        "https://musicbrainz.org:443/release/076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8",
        "//musicbrainz.org/release/076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8",
        "https://www.deezer.com/%2f%2f127.0.0.1/album/302127",
        "\x00https://musicbrainz.org/release/076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8",
        "https://musicbrainz.org/release/076ad60e-2b19-31b0-9c02-a5a2f0a4b\n1c8",
    ],
)
def test_rejects_non_allowlisted_or_ambiguous_urls(url: str) -> None:
    with pytest.raises(MalformedCandidateUrl):
        recognize_candidate_url(url)


@pytest.mark.parametrize(
    ("url", "provider", "candidate_type"),
    [
        ("https://www.deezer.com/playlist/123", "deezer", "playlist"),
        ("https://www.discogs.com/master/123", "discogs", "master"),
        (
            "https://musicbrainz.org/recording/076ad60e-2b19-31b0-9c02-a5a2f0a4b1c8",
            "musicbrainz",
            "recording",
        ),
    ],
)
def test_reports_allowlisted_provider_but_unsupported_type(
    url: str, provider: str, candidate_type: str
) -> None:
    with pytest.raises(UnsupportedCandidateUrl) as caught:
        recognize_candidate_url(url)

    assert caught.value.provider == provider
    assert caught.value.candidate_type == candidate_type
