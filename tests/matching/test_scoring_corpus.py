"""Scoring regression corpus (docs/product-spec.md's testing strategy): ~real-
world album/singleton scenarios as YAML (local tags + candidates +
expected winner), run as a top-1 accuracy regression.

"The highest-value test asset in the project" — when someone tunes a
weight in matching/weights.py, this shows immediately what broke.
Deliberately two separate corpora (tests/fixtures/matching/album/ and
.../singleton/): album and singleton matching are different problems
with different failure modes (see matching/engine.py), so a shared
corpus would blur which path a regression actually hit.

Grows over time — add a new YAML file whenever a real mismatch is
found, per the plan's "maintained forever" framing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from muzilla.domain.metadata import TrackMeta
from muzilla.matching.engine import propose_for_group, propose_for_singleton
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate

FIXTURES = Path(__file__).parent.parent / "fixtures" / "matching"


def _load_scenarios(subdir: str) -> list[tuple[str, dict[str, Any]]]:
    directory = FIXTURES / subdir
    scenarios = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        scenarios.append((data["name"], data))
    return scenarios


def _candidate_from_yaml(c: dict[str, Any]) -> ReleaseCandidate:
    tracks = tuple(
        CandidateTrack(
            position=i + 1,
            title=t["title"],
            duration_ms=t.get("duration_ms"),
            isrc=t.get("isrc"),
        )
        for i, t in enumerate(c.get("tracks", []))
    )
    return ReleaseCandidate(
        source=c["source"],
        ref=ProviderRef(provider=c["source"], id=c["ref_id"]),
        album=c.get("album"),
        album_artist=c.get("album_artist"),
        year=c.get("year"),
        original_year=c.get("original_year"),
        label=c.get("label"),
        catalog_number=c.get("catalog_number"),
        barcode=c.get("barcode"),
        tracks=tracks,
    )


_ALBUM_SCENARIOS = _load_scenarios("album")
_SINGLETON_SCENARIOS = _load_scenarios("singleton")


@pytest.mark.parametrize("name,data", _ALBUM_SCENARIOS, ids=[n for n, _ in _ALBUM_SCENARIOS])
def test_album_scoring_corpus(name: str, data: dict[str, Any]) -> None:
    local = data["local"]
    local_tracks = [
        TrackMeta(title=t["title"], duration_ms=t.get("duration_ms"))
        for t in local["tracks"]
    ]
    candidates = [_candidate_from_yaml(c) for c in data["candidates"]]

    result = propose_for_group(
        local_tracks,
        candidates,
        album=local.get("album"),
        album_artist=local.get("album_artist"),
        year=local.get("year"),
        label=local.get("label"),
        catalog_number=local.get("catalog_number"),
        barcode=local.get("barcode"),
    )

    expected = data.get("expected_winner")
    if expected is None:
        # No correct candidate exists in the pool -- the point of the
        # scenario is that nothing should look confident enough to
        # auto-apply, not that a specific ranking is wrong.
        assert not result.decision.auto_applicable, (
            f"{name}: expected no auto-applicable match, got "
            f"{result.ranked[0].candidate.source}:{result.ranked[0].candidate.ref.id} "
            f"at distance {result.ranked[0].adjusted_distance:.3f}"
        )
        return

    assert result.ranked, f"{name}: expected a winner but got no candidates at all"
    top = result.ranked[0]
    assert (top.candidate.source, top.candidate.ref.id) == (
        expected["source"],
        expected["ref_id"],
    ), (
        f"{name}: expected top candidate {expected}, got "
        f"{top.candidate.source}:{top.candidate.ref.id} (distance={top.distance:.3f})"
    )

    if "expect_auto_applicable" in data:
        assert result.decision.auto_applicable == data["expect_auto_applicable"], (
            f"{name}: auto_applicable={result.decision.auto_applicable} "
            f"(distance={top.adjusted_distance:.3f})"
        )
    if "expect_needs_confirmation" in data:
        assert result.decision.needs_confirmation == data["expect_needs_confirmation"]
    if data.get("expect_duplicate_flagged"):
        assert top.is_duplicate_of != (), f"{name}: expected winner to be duplicate-flagged"


@pytest.mark.parametrize(
    "name,data", _SINGLETON_SCENARIOS, ids=[n for n, _ in _SINGLETON_SCENARIOS]
)
def test_singleton_scoring_corpus(name: str, data: dict[str, Any]) -> None:
    local = data["local"]
    local_meta = TrackMeta(
        title=local.get("title"),
        artist=local.get("artist"),
        duration_ms=local.get("duration_ms"),
        isrc=local.get("isrc"),
    )
    candidates = [_candidate_from_yaml(c) for c in data["candidates"]]

    result = propose_for_singleton(local_meta, candidates)

    expected = data.get("expected_winner")
    if expected is None:
        assert not result.decision.auto_applicable, (
            f"{name}: expected no auto-applicable match, got "
            f"{result.ranked[0].candidate.source}:{result.ranked[0].candidate.ref.id}"
        )
        return

    assert result.ranked, f"{name}: expected a winner but got no candidates at all"
    top = result.ranked[0]
    assert (top.candidate.source, top.candidate.ref.id) == (
        expected["source"],
        expected["ref_id"],
    ), (
        f"{name}: expected top candidate {expected}, got "
        f"{top.candidate.source}:{top.candidate.ref.id} (distance={top.distance:.3f})"
    )

    if "expect_auto_applicable" in data:
        assert result.decision.auto_applicable == data["expect_auto_applicable"], (
            f"{name}: auto_applicable={result.decision.auto_applicable} "
            f"(distance={top.adjusted_distance:.3f})"
        )


def test_corpus_is_not_empty() -> None:
    """A silent glob-pattern typo would make the parametrized tests
    above pass vacuously (0 cases collected) -- guard against that."""
    assert len(_ALBUM_SCENARIOS) >= 5
    assert len(_SINGLETON_SCENARIOS) >= 3
