from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.db.models import ProviderCache
from muzilla.providers.cache import (
    _HISHEL_FILE_TTL_SECONDS,
    HttpClientConfig,
    build_http_client,
    cache_get,
    cache_put,
    query_hash,
)


def test_cache_miss_returns_none(db_session: Session) -> None:
    key = query_hash("musicbrainz", "search_releases", "nothing here")
    assert cache_get(db_session, "musicbrainz", "search_releases", key) is None


def test_cache_put_then_get_round_trips(db_session: Session) -> None:
    key = query_hash("deezer", "search_releases", "abbey road")
    cache_put(db_session, "deezer", "search_releases", key, {"hits": [1, 2, 3]})
    assert cache_get(db_session, "deezer", "search_releases", key) == {"hits": [1, 2, 3]}


def test_cache_put_overwrites_existing_entry(db_session: Session) -> None:
    key = query_hash("deezer", "get_release", "release-1")
    cache_put(db_session, "deezer", "get_release", key, {"v": 1})
    cache_put(db_session, "deezer", "get_release", key, {"v": 2})
    assert cache_get(db_session, "deezer", "get_release", key) == {"v": 2}


def test_cache_expired_entry_is_treated_as_miss(db_session: Session) -> None:
    key = query_hash("musicbrainz", "get_release", "mbid-1")
    db_session.add(
        ProviderCache(
            provider="musicbrainz",
            operation="get_release",
            query_hash=key,
            payload={"v": 1},
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.commit()
    assert cache_get(db_session, "musicbrainz", "get_release", key) is None


def test_cache_keys_are_scoped_per_operation(db_session: Session) -> None:
    key = query_hash("musicbrainz", "x", "same-query")
    cache_put(db_session, "musicbrainz", "search_releases", key, {"op": "search"})
    cache_put(db_session, "musicbrainz", "get_release", key, {"op": "get"})
    assert cache_get(db_session, "musicbrainz", "search_releases", key) == {"op": "search"}
    assert cache_get(db_session, "musicbrainz", "get_release", key) == {"op": "get"}


def test_query_hash_is_stable_and_order_sensitive() -> None:
    assert query_hash("a", "b", "c") == query_hash("a", "b", "c")
    assert query_hash("a", "b", "c") != query_hash("a", "c", "b")


def test_build_http_client_bounds_the_on_disk_hishel_store(tmp_path: Path) -> None:
    """hishel's AsyncFileStorage only prunes
    stale files when constructed with a `ttl` — left unset (the prior
    state of this code), the on-disk cache grows without bound
    regardless of how often sweep_provider_cache() prunes the DB-side
    ProviderCache rows, since those are separate stores."""
    client = build_http_client(
        HttpClientConfig(
            base_url="https://example.invalid",
            user_agent="muzilla-test/1",
            cache_dir=tmp_path,
        )
    )
    storage = client._transport._storage  # type: ignore[attr-defined]
    assert storage._ttl == _HISHEL_FILE_TTL_SECONDS
