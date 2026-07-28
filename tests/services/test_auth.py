from __future__ import annotations

import time

import pytest

from muzilla.config.schema import AuthConfig
from muzilla.services import auth


def _config(password: str = "hunter2", secret: str = "s3cr3t") -> AuthConfig:
    return AuthConfig(enabled=True, password=password, session_secret=secret)


def test_verify_password_correct() -> None:
    password_hash = auth.hash_password("hunter2")
    assert auth.verify_password(password_hash, "hunter2") is True


def test_verify_password_incorrect() -> None:
    password_hash = auth.hash_password("hunter2")
    assert auth.verify_password(password_hash, "wrong") is False


def test_session_cookie_roundtrip() -> None:
    config = _config()
    cookie = auth.create_session_cookie(config, epoch=0)
    token = auth.verify_session_cookie(config, cookie)
    assert token is not None
    assert not token.is_expired()


def test_session_cookie_rejects_tampering() -> None:
    config = _config()
    cookie = auth.create_session_cookie(config, epoch=0)
    payload, sig = cookie.split(".", 1)
    tampered = f"{payload}x.{sig}"
    assert auth.verify_session_cookie(config, tampered) is None


def test_session_cookie_rejects_wrong_secret() -> None:
    cookie = auth.create_session_cookie(_config(secret="secret-a"), epoch=0)
    other_config = _config(secret="secret-b")
    assert auth.verify_session_cookie(other_config, cookie) is None


def test_session_cookie_rejects_garbage() -> None:
    assert auth.verify_session_cookie(_config(), "not-a-valid-cookie") is None


def test_session_token_expiry() -> None:
    token = auth.SessionToken(issued_at=0, epoch=0)
    assert token.is_expired(now=auth._SESSION_TTL_SECONDS + 1)
    assert not token.is_expired(now=1)


def test_create_session_cookie_raises_when_no_secret() -> None:
    config = AuthConfig(enabled=True, password="hunter2", session_secret=None)
    with pytest.raises(auth.AuthNotConfiguredError):
        auth.create_session_cookie(config, epoch=0)


def test_time_travel_smoke() -> None:
    # sanity check that is_expired uses wall-clock time by default
    token = auth.SessionToken(issued_at=int(time.time()), epoch=0)
    assert not token.is_expired()


def test_token_from_current_epoch_is_not_revoked() -> None:
    token = auth.SessionToken(issued_at=int(time.time()), epoch=3)
    assert not token.is_revoked(current_epoch=3)


def test_token_from_a_prior_epoch_is_revoked() -> None:
    token = auth.SessionToken(issued_at=int(time.time()), epoch=2)
    assert token.is_revoked(current_epoch=3)


def test_session_cookie_carries_the_issuing_epoch() -> None:
    config = _config()
    cookie = auth.create_session_cookie(config, epoch=5)
    token = auth.verify_session_cookie(config, cookie)
    assert token is not None
    assert token.epoch == 5
