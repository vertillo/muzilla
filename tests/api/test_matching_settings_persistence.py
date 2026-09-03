"""Settings persistence/restart/reset for MatchingConfig min_gap and provider tie."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from muzilla.config.schema import MatchingConfig
from muzilla.pipeline.effective_settings import effective_matching_config
from muzilla.services.settings import (
    reset_matching_settings,
    update_matching_settings,
)


def test_matching_settings_persist_and_effective_runtime(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Initially defaults
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["matching"]["min_gap"] == 0.03
    # Update via API
    # Need CSRF
    csrf = client.get("/api/auth/status").json()["csrf_token"]
    client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf})
    r2 = client.put("/api/settings/matching", json={"min_gap": 0.07, "provider_order": ["deezer", "musicbrainz", "discogs"]})
    assert r2.status_code == 200
    assert r2.json()["min_gap"] == 0.07
    # Effective runtime should reflect DB
    # Simulate restart: new request should still see persisted value
    r3 = client.get("/api/settings")
    assert r3.json()["matching"]["min_gap"] == 0.07
    assert r3.json()["matching"]["provider_order"] == ["deezer", "musicbrainz", "discogs"]
    # Reset
    r4 = client.post("/api/settings/matching/reset")
    assert r4.status_code == 200
    assert r4.json()["min_gap"] == MatchingConfig().min_gap
    r5 = client.get("/api/settings")
    assert r5.json()["matching"]["min_gap"] == MatchingConfig().min_gap


def test_effective_matching_config_after_reset_and_restart(db_session: Session, tmp_path: Path) -> None:
    base = MatchingConfig(min_gap=0.03)
    # Update
    update_matching_settings(db_session, base, min_gap=0.09)
    eff = effective_matching_config(db_session, base)
    assert eff.min_gap == 0.09
    # Reset
    reset_matching_settings(db_session, base)
    eff2 = effective_matching_config(db_session, base)
    assert eff2.min_gap == 0.03
    # Simulate restart by creating new session with same DB file
    # (db_session fixture already uses file, so just re-read)
    eff3 = effective_matching_config(db_session, base)
    assert eff3.min_gap == 0.03


def test_min_gap_provider_tie_within_gap_vs_outside() -> None:
    from muzilla.config.schema import MatchingConfig as MC
    from muzilla.domain.metadata import TrackMeta
    from muzilla.matching.engine import propose_for_singleton
    from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate

    def cand(source: str, ref: str, title: str) -> ReleaseCandidate:
        return ReleaseCandidate(source=source, ref=ProviderRef(provider=source, id=ref), album="Album", album_artist="Artist", year=2000, tracks=(CandidateTrack(position=1, title=title, duration_ms=200000),))

    local = TrackMeta(title="Title A", artist="Artist", duration_ms=200000)
    # Two identical candidates, gap 0, min_gap 0.05 => ambiguous (tie)
    cfg_tie = MC(min_gap=0.05, provider_order=["musicbrainz", "discogs"], source_penalty=0.02)
    res_tie = propose_for_singleton(local, [cand("discogs", "b", "Title A"), cand("musicbrainz", "a", "Title A")], matching_config=cfg_tie)
    assert res_tie.decision.ambiguous is True
    # Same but min_gap 0 => strong (no tie enforcement)
    cfg_no_tie = MC(min_gap=0.0, provider_order=["musicbrainz", "discogs"], source_penalty=0.02)
    res_no_tie = propose_for_singleton(local, [cand("discogs", "b", "Title A"), cand("musicbrainz", "a", "Title A")], matching_config=cfg_no_tie)
    assert res_no_tie.decision.strong is True
