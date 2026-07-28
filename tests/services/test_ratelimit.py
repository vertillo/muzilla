from __future__ import annotations

from muzilla.services.ratelimit import FixedWindowLimiter


def test_allows_up_to_limit_attempts_in_one_window() -> None:
    limiter = FixedWindowLimiter(limit=3, window_seconds=60)
    assert limiter.check("a", now=0.0) is None
    assert limiter.check("a", now=0.0) is None
    assert limiter.check("a", now=0.0) is None


def test_rejects_the_attempt_past_the_limit() -> None:
    limiter = FixedWindowLimiter(limit=3, window_seconds=60)
    for _ in range(3):
        limiter.check("a", now=0.0)
    retry_after = limiter.check("a", now=0.0)
    assert retry_after is not None
    assert retry_after == 60


def test_window_resets_after_window_seconds() -> None:
    limiter = FixedWindowLimiter(limit=1, window_seconds=60)
    assert limiter.check("a", now=0.0) is None
    assert limiter.check("a", now=59.0) is not None
    assert limiter.check("a", now=60.0) is None


def test_keys_are_independent() -> None:
    limiter = FixedWindowLimiter(limit=1, window_seconds=60)
    assert limiter.check("a", now=0.0) is None
    assert limiter.check("b", now=0.0) is None
    assert limiter.check("a", now=0.0) is not None


def test_reset_clears_the_window_for_a_key() -> None:
    limiter = FixedWindowLimiter(limit=1, window_seconds=60)
    limiter.check("a", now=0.0)
    limiter.reset("a")
    assert limiter.check("a", now=0.0) is None


def test_reset_is_a_noop_for_an_unseen_key() -> None:
    limiter = FixedWindowLimiter(limit=1, window_seconds=60)
    limiter.reset("never-seen")  # must not raise
