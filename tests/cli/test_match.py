from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

import muzilla.cli.commands.match as match_cli
from muzilla.cli.main import app
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Track, TrackGroup
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate, ReleaseQuery
from muzilla.services.providers import ProviderSet

runner = CliRunner()


@dataclass
class StubProvider:
    releases: dict[str, ReleaseCandidate] = field(default_factory=dict)
    search_results: list[ReleaseCandidate] = field(default_factory=list)

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        return self.search_results[:limit]

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
        return self.releases.get(ref.id)


def _release() -> ReleaseCandidate:
    return ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id="release-1"),
        album="Test Album",
        album_artist="Test Artist",
        year=2000,
        mb_release_id="release-1",
        tracks=(CandidateTrack(position=1, title="Track One", duration_ms=100_000),),
    )


@pytest.fixture(autouse=True)
def stub_build_provider_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every CLI test in this file runs against a stub provider, never
    real network — build_provider_set is monkeypatched at the import
    site used by cli/commands/match.py."""
    stub = StubProvider(releases={"release-1": _release()}, search_results=[_release()])
    stub_set = ProviderSet(metadata={"musicbrainz": stub}, art={}, lyrics={}, fingerprint={}, clients=())  # type: ignore[arg-type]
    monkeypatch.setattr(match_cli, "build_provider_set", lambda config: stub_set)


def _seed_group(db_path: Path) -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    with factory() as session:
        group = TrackGroup(key="k1", album="Test Album", album_artist="Test Artist")
        session.add(group)
        session.flush()
        t = Track(
            path="/music/a.mp3", filename="a.mp3", ext="mp3", size_bytes=1, mtime_ns=1,
            title="Track One", album="Test Album", album_artist="Test Artist",
            duration_ms=100_000, group_id=group.id,
        )
        session.add(t)
        session.commit()
        session.refresh(group)
        return group.id


def _seed_track(db_path: Path) -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    with factory() as session:
        t = Track(
            path="/music/single.mp3", filename="single.mp3", ext="mp3",
            size_bytes=1, mtime_ns=1, title="Track One", duration_ms=100_000,
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        return t.id


def test_match_group_lists_candidates(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    group_id = _seed_group(migrated_db)

    result = runner.invoke(app, ["match", "group", str(group_id)])
    assert result.exit_code == 0, result.output
    assert "musicbrainz" in result.output
    assert "Test Album" in result.output


def test_match_group_unknown_group_errors(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))

    result = runner.invoke(app, ["match", "group", "99999"])
    assert result.exit_code == 1
    # legacy ChangeSet staging removed, but unknown group should still error; check both output and exception for robustness
    combined = result.output + str(result.exception or "")
    assert "not found" in combined.lower()


def test_match_group_stage_creates_changeset(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    group_id = _seed_group(migrated_db)

    result = runner.invoke(app, ["match", "group", str(group_id), "--stage", "musicbrainz:release-1"])
    assert result.exit_code == 0, result.output
    # legacy ChangeSet staging removed per COMPAT-CHANGESET-001 / migration 0019
    assert "staging via changeset is removed" in result.output.lower()


def test_match_group_stage_bad_format_errors(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    group_id = _seed_group(migrated_db)

    result = runner.invoke(app, ["match", "group", str(group_id), "--stage", "not-valid"])
    # legacy ChangeSet staging removed per COMPAT-CHANGESET-001 - now just prints staging removed message, no format validation
    assert result.exit_code == 0, result.output
    assert "staging via changeset is removed" in result.output.lower()


def test_match_track_lists_candidates(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    track_id = _seed_track(migrated_db)

    result = runner.invoke(app, ["match", "track", str(track_id)])
    assert result.exit_code == 0, result.output
    assert "musicbrainz" in result.output


def test_match_track_stage_creates_changeset(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    track_id = _seed_track(migrated_db)

    result = runner.invoke(app, ["match", "track", str(track_id), "--stage", "musicbrainz:release-1"])
    assert result.exit_code == 0, result.output
    # legacy ChangeSet staging removed per COMPAT-CHANGESET-001 / migration 0019
    assert "staging via changeset is removed" in result.output.lower()
