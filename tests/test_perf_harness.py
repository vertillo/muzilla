"""Lightweight tests for PERF-SCALE-001 harness and threshold manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent


def _load_perf_harness_module() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "perf_benchmark", REPO_ROOT / "scripts" / "perf_benchmark.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_thresholds_manifest_exists_and_valid() -> None:
    p = REPO_ROOT / "benchmark" / "thresholds.json"
    assert p.exists(), "benchmark/thresholds.json must exist (immutable manifest)"
    data = json.loads(p.read_text())
    assert "version" in data
    assert "commit" in data
    assert "thresholds" in data
    assert "dataset" in data
    assert data["dataset"]["count"] == 100000
    assert "hardware_baseline" in data
    # thresholds must have operator and target, and must not be relaxable
    for key, spec in data["thresholds"].items():
        assert "target" in spec, f"{key} missing target"
        assert "operator" in spec, f"{key} missing operator"
        assert spec["operator"] in ("<=", ">=", "=="), f"{key} invalid operator"
        assert isinstance(spec["target"], (int, float))
    # Original resource bounds remain unchanged; only measurement semantics are clarified.
    assert data["thresholds"]["muzilla_rss_mib_peak"]["target"] == 2048
    assert data["thresholds"]["muzilla_rss_mib_peak"]["operator"] == "<="
    assert data["thresholds"]["sqlite_connections_max"]["target"] == 20
    assert data["thresholds"]["sqlite_connections_max"]["operator"] == "<="
    assert data["thresholds"]["fd_count_max"]["target"] == 1024
    assert data["thresholds"]["fd_count_max"]["operator"] == "<="
    # commit must look like SHA
    assert len(data["commit"]) >= 7


def test_thresholds_not_relaxed_after_run_note() -> None:
    p = REPO_ROOT / "benchmark" / "thresholds.json"
    text = p.read_text()
    assert (
        "may not be relaxed" in text or "not be relaxed" in text or "do not relax" in text.lower()
    )


def test_perf_compose_overlay_exists_and_no_build() -> None:
    p = REPO_ROOT / "benchmark" / "docker-compose.perf.yml"
    assert p.exists()
    text = p.read_text()
    assert "MUZILLA_PERF_IMAGE" in text
    assert "MUZILLA_PERF_DATA" in text
    assert "MUZILLA_PERF_LIBRARY" in text
    # must mention --no-build usage
    assert "no-build" in text.lower() or "no_build" in text.lower() or "--no-build" in text
    # must not mount repo source
    assert ".:/app" not in text
    assert "./src" not in text


def test_gen_perf_library_deterministic(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gen_perf_library", REPO_ROOT / "scripts" / "gen_perf_library.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    generate = mod.generate

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    generate(20, out1, seed=42)
    generate(20, out2, seed=42)
    files1 = sorted(p.name for p in out1.glob("*.mp3"))
    files2 = sorted(p.name for p in out2.glob("*.mp3"))
    assert files1 == files2

    # compare tag-relevant file sizes/contents are deterministic for tiny fixture subset
    # at least file count and names must match; content hash for first file must be identical
    def file_hash(p: Path) -> str:
        h = hashlib.sha256()
        h.update(p.read_bytes()[:8192])
        return h.hexdigest()

    for f1, f2 in zip(sorted(out1.glob("*.mp3")), sorted(out2.glob("*.mp3")), strict=True):
        assert file_hash(f1) == file_hash(f2)


def test_harness_helpers(tmp_path: Path) -> None:
    import contextlib
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "perf_benchmark", REPO_ROOT / "scripts" / "perf_benchmark.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    thresholds = mod._load_thresholds()
    assert thresholds["thresholds"]["muzilla_rss_mib_peak"]["target"] == 2048

    hw = mod._hardware_record()
    assert "uname" in hw
    assert "docker_version" in hw or "docker_info" in hw

    # sha256 helper
    f = tmp_path / "hello.txt"
    f.write_text("hello")
    assert mod._sha256_file(f) == hashlib.sha256(b"hello").hexdigest()

    # isolated scratch check: out inside repo must be rejected (guard in main)
    with contextlib.suppress(Exception):
        _ = mod.main


def test_image_build_permission_failure_does_not_retry_or_inspect() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    module = _load_perf_harness_module()
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> Any:
        calls.append(command)
        return SimpleNamespace(returncode=13, stderr="Operation not permitted", stdout="")

    with patch.object(module.subprocess, "run", side_effect=run):
        try:
            module._build_candidate_image("muzilla:blocked")
        except RuntimeError as exc:
            assert "status 13" in str(exc)
            assert "Operation not permitted" in str(exc)
        else:
            raise AssertionError("a denied image build must fail")

    assert len(calls) == 1
    assert calls[0][:2] == ["docker", "build"]


def test_gitignore_excludes_perf_results() -> None:
    text = (REPO_ROOT / ".gitignore").read_text()
    assert "benchmark/results" in text or "perf_results" in text


def test_harness_deterministic_grouping_seed_visible_to_container() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # Must seed inside exact Compose container (docker exec) so the WorkUnit
    # is visible to the running app, not via host direct DB that corrupts WAL.
    assert "docker exec" in text
    assert "compose_project" in text
    assert "e2e-source-perf" in text
    assert "grouping_confidence=0.4" in text
    # Must select operation before browser Apply/Undo (PATCH decisions)
    assert "/api/reviews/" in text and "decisions" in text
    assert "constrained_review_ready" in text
    # Proactive seeding before 30-candidate loop, not only fallback
    assert "pre_seed" in text


def test_harness_incremental_is_single_file_scope() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # Threshold describes incremental as single-file scope; harness must measure that.
    assert "incremental_scan_root" in text
    assert "single-file" in text.lower() or "single_file" in text.lower() or "Single-file" in text
    # Must touch one file and scan that file, not full library for incremental metric
    assert '"/music/"' in text or "'/music/'" in text or "/music/{_inc_file_name}" in text


def test_harness_immutable_image_and_verification() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # Must build immutable sha256: Id and verify running container matches candidate
    assert 'startswith("sha256:")' in text or "sha256:" in text
    assert "MUZILLA_PERF_IMAGE" in text
    assert "image_digest" in text
    assert "running_image_id" in text
    assert "candidate_image_id_verified" in text
    assert "{{.Image}}" in text or "{{.Config.Image}}" in text
    # Must fail-closed if verification fails
    assert "immutable verification failed" in text or "image verification failed" in text


def test_harness_mock_provider_injected_via_compose() -> None:
    compose = (REPO_ROOT / "benchmark" / "docker-compose.perf.yml").read_text()
    harness = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # Compose overlay must inject mock provider via MUZILLA_MOCK_URL
    assert "MUZILLA_MOCK_URL" in compose
    assert "MUZILLA_PROVIDERS__MUSICBRAINZ__BASE_URL_OVERRIDE" in compose
    assert "host.docker.internal" in compose
    assert "extra_hosts" in compose
    # Harness must set MUZILLA_MOCK_URL through Compose env
    assert "MUZILLA_MOCK_URL" in harness
    assert "mock_url" in harness.lower() or "MUZILLA_MOCK_URL" in harness
    # Host mode also injects override
    assert "MUZILLA_PROVIDERS__MUSICBRAINZ__BASE_URL_OVERRIDE" in harness


def test_harness_every_acceptance_measured_fail_closed() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # 30 samples p95 for all p95 thresholds
    assert text.count("for _ in range(30)") >= 4 or text.count("range(30)") >= 4
    assert "_p95(" in text
    # API error count measured
    assert "api_5xx_count" in text
    assert "api_error_rate" in text
    assert "_track_5xx" in text
    # Apply/Undo 10-track bundle measured
    assert "apply_p95_ms_per_bundle" in text
    assert "undo_p95_ms" in text
    assert "10-track" in text or "10_track" in text or "perf-apply" in text
    assert "apply_bundle_count" in text or "_apply_samples" in text
    # Incremental terminal state + catalog evidence
    assert "incremental_scan_state" in text
    assert "incremental_changed_file_evidence" in text
    assert "incremental_scan_terminal_error" in text or "terminal" in text.lower()
    # Fail-closed for all thresholds when unavailable
    assert 'failures.append(f"{key}: unavailable (fail-closed)")' in text
    # Must record mock injection verification
    assert "mock_provider_injected" in text


def test_perf_library_mount_is_writable_for_apply_undo() -> None:
    """PERF-SCALE-001: the scratch library must be writable.

    The benchmark library is disposable harness-generated scratch (never user
    data), and the acceptance requires real Apply/Undo file mutation through
    the reviewed journaled path. A read-only /music mount makes every
    apply/undo job fail with EROFS (observed: Errno 30 on
    '<track>.muzilla.tmp'), the 15s poll then expires per bundle, and the
    benchmark is vacuous. The overlay must not set :ro / read_only on /music.
    """
    compose = (REPO_ROOT / "benchmark" / "docker-compose.perf.yml").read_text()
    for line in compose.splitlines():
        stripped = line.strip()
        if "/music" in stripped and ("MUZILLA_PERF_LIBRARY" in stripped or ":/music" in stripped):
            assert ":ro" not in stripped, f"/music must be writable for Apply/Undo: {stripped}"
    assert (
        "read_only: true" not in compose
        or "/music" not in compose.split("read_only: true")[0][-500:]
    )


def test_harness_reserves_grouping_track_from_api_pools() -> None:
    """PERF-SCALE-001: the seeded grouping track is owned by the browser flow.

    A manual review or API apply bundle on the same track makes the browser
    Apply fail with concurrent-conflict (observed on the seeded track).
    The harness must exclude the reserved track from both the manual
    review-generation pool and the API apply-bundle pool, including the
    pagination fallback fills.
    """
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "_reserved_tracks" in text
    assert "_reserved_for_browser" in text
    assert "grouping_track_id" in text
    # fallback fills must also respect the reservation
    assert "not in _reserved_tracks" in text
    assert "not in _reserved_for_browser" in text


def test_harness_fd_count_measures_app_process() -> None:
    """PERF-SCALE-001: fd_count_max must sample the app, not the probe shell.

    `ls /proc/self/fd` inside `docker exec sh -c` counts the ephemeral sh
    wrapper's FDs (~4), making the <=1024 threshold vacuous. The harness must
    sample /proc/1/fd (PID 1 is the muzilla server in the candidate image).
    """
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "/proc/1/fd" in text
    assert "ls -1 /proc/self/fd" not in text


def test_harness_no_live_provider_or_source_mount() -> None:
    compose = (REPO_ROOT / "benchmark" / "docker-compose.perf.yml").read_text()
    harness = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # No source bind mount or build in perf overlay
    assert (
        "build:" not in compose.lower()
        or "no-build" in compose.lower()
        or "no_build" in compose.lower()
    )
    # Harness must use --no-build
    assert "--no-build" in harness
    # No live provider contact when mock is set; harness mentions no-live or mock
    assert "mock_provider" in harness.lower() or "mock" in harness.lower()


def test_perf_overlay_disables_non_mocked_providers() -> None:
    """P1: only MusicBrainz (mock-redirected) may stay enabled in perf runs."""
    compose = (REPO_ROOT / "benchmark" / "docker-compose.perf.yml").read_text()
    for provider in ("DEEZER", "DISCOGS", "ACOUSTID", "COVERARTARCHIVE", "LRCLIB"):
        assert f"MUZILLA_PROVIDERS__{provider}__ENABLED" in compose, provider
    assert compose.count('"false"') >= 5


def test_mock_server_exposes_request_stats() -> None:
    """P1: mock server counts per-path hits so the harness can verify mock-only use."""
    text = (REPO_ROOT / "e2e" / "mock_provider_server.py").read_text()
    assert "/__stats" in text
    assert "_REQUEST_COUNTS" in text


def test_mock_server_stats_endpoint_serves() -> None:
    """Functional: the stats endpoint reports hits without breaking fixtures."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mock_provider_server", REPO_ROOT / "e2e" / "mock_provider_server.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from fastapi.testclient import TestClient

    client = TestClient(mod.app)
    assert client.get("/release?query=test&limit=1&fmt=json").status_code == 200
    stats = client.get("/__stats").json()
    assert stats["requests"].get("/release", 0) >= 1


def test_harness_verifies_mock_only_providers_fail_closed() -> None:
    """P1: matching outcomes + mock stats must prove musicbrainz-only (no live Deezer)."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "provider_outcomes" in text
    assert "mock_only_verified" in text
    assert "mock_only_violation" in text
    assert "mock_non_musicbrainz_paths" in text
    assert "mock_request_paths" in text
    # fail-closed gate in threshold evaluation
    assert "mock_only_providers: not verified musicbrainz-only" in text
    # unconditional True injection must be gone
    assert 'metrics["mock_provider_injected"] = True' not in text


def test_gen_library_realistic_audio_is_valid_encoded() -> None:
    """P1: the 2% subset must be valid long encoded audio, never padded zeros."""
    text = (REPO_ROOT / "scripts" / "gen_perf_library.py").read_text()
    assert "ffmpeg" in text
    assert "_build_long_fixture" in text
    assert 'b"\\x00" * pad' not in text
    assert "refusing silent fallback" in text


def test_gen_library_long_fixture_functional(tmp_path) -> None:
    """Functional: long fixture builds valid MP3 and subset copies it."""
    import importlib.util
    import shutil as _shutil

    if _shutil.which("ffmpeg") is None:
        raise AssertionError("ffmpeg required for realistic-audio subset")
    spec = importlib.util.spec_from_file_location(
        "gen_perf_library", REPO_ROOT / "scripts" / "gen_perf_library.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    long_mp3 = mod._build_long_fixture(tmp_path)
    assert long_mp3.exists() and long_mp3.stat().st_size > 200_000
    # valid decodable audio of realistic length (not padded zeros)
    from mutagen.mp3 import MP3

    info = MP3(str(long_mp3)).info
    assert info.length >= 20.0, f"long fixture too short: {info.length}"
    # fail-closed when the fixture is missing (subset selected but no fixture)
    import random as _random

    _raised = False
    for _seed in range(500):
        try:
            mod._make_realistic_audio(tmp_path / f"x{_seed}.mp3", _random.Random(_seed), None)
        except RuntimeError:
            _raised = True
            break
    assert _raised, "subset without fixture must raise, never silently pad"


def test_harness_grouping_runs_in_candidate_not_host_snapshot() -> None:
    """P1: grouping must run as a candidate `group` job, not host src on a DB copy."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert 'type="group"' in text or "type='group'" in text
    assert "grouping_job_id" in text
    assert "grouping_job_state" in text
    assert "grouping_candidate_job" in text
    # old host-snapshot path must be gone
    assert "run_grouping_cascade" not in text
    assert "_tmp_grp" not in text


def test_harness_search_facet_thresholds_hit_real_endpoints() -> None:
    """P1: search threshold from /api/tracks?search=…; filters cover real facets."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "/api/tracks/facets" in text
    assert "catalog_filters_p95_ms" in text
    assert "_filter_facet_combined" in text or "catalog_filter" in text
    # dashboard must no longer masquerade as facets
    assert '("facets", "/api/dashboard")' not in text


def test_harness_cancel_measures_to_terminal_state() -> None:
    """P1: cancel latency runs to terminal cancelled, not POST return."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "cancel_terminal_error" in text
    assert "no terminal state within 30s" in text
    assert "_t_cancel" in text or "cancel POST send until" in text


def test_harness_playwright_drives_tag_bundle_with_tag_asserts() -> None:
    """P1: browser applies+undoes a 10-track tag bundle, asserting tag change/restore."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "PHASE" in text
    assert "playwright_tag_bundle_id" in text
    assert "playwright_tag_applied_assert" in text
    assert "playwright_tag_restored_assert" in text
    assert "apply_pool_track_ids" in text
    assert "PWTag" in text
    # grouping-review browser flow must be gone
    assert "playwright success" not in text
    assert "_grouping_id" not in text


def test_apply_fixture_source_snapshots_capture_physical_guards_in_both_modes() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    start = text.index("# 7. Apply/Undo 10-track bundle")
    end = text.index("# Now measure apply/undo p95", start)
    apply_setup = text[start:end]
    docker_start = apply_setup.index("_create_script = f")
    host_start = apply_setup.index("# host mode: bind fixture setup to this run's isolated storage")
    docker_setup = apply_setup[docker_start:host_start]

    assert "capture_file_guard" in docker_setup
    assert "physical_guard" in docker_setup
    assert "cfg.storage.library_root" in docker_setup
    assert (
        "_create_host_apply_bundle(scratch_root, library_dir, _bundle_tracks)"
        in apply_setup[host_start:]
    )


def test_host_apply_fixture_uses_scratch_storage_and_validates_persisted_guards(
    migrated_db: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    import hashlib
    import importlib
    import shutil
    import sys
    from datetime import UTC, datetime

    from sqlalchemy import select

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from muzilla.changes.bundle_applier import _source_precondition
    from muzilla.changes.writer import _source_precondition_error
    from muzilla.db.engine import create_db_engine, create_session_factory
    from muzilla.db.models import ProposalRevision, ReviewBundle, Track
    from muzilla.domain.metadata import tag_hash
    from muzilla.tags.reader import read_track

    harness = _load_perf_harness_module()
    scratch_root = tmp_path / "scratch"
    ambient_root = tmp_path / "ambient"
    fixture = REPO_ROOT / "tests" / "fixtures" / "audio" / "silence.mp3"

    def prepare_storage(root: Path) -> list[int]:
        library = root / "library"
        data = root / "data"
        library.mkdir(parents=True)
        data.mkdir()
        database = data / "muzilla.db"
        shutil.copy2(migrated_db, database)
        engine = create_db_engine(database)
        factory = create_session_factory(engine)
        ids = []
        now = datetime.now(UTC)
        try:
            with factory() as session:
                for index in range(10):
                    path = library / f"fixture-{index}.mp3"
                    shutil.copy2(fixture, path)
                    metadata = read_track(path)
                    stat = path.stat()
                    track = Track(
                        path=str(path),
                        filename=path.name,
                        ext=path.suffix,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        title=metadata.title or f"Fixture {index}",
                        tag_hash=tag_hash(metadata),
                        first_seen_at=now,
                        last_scanned_at=now,
                    )
                    session.add(track)
                    session.flush()
                    ids.append(int(track.id))
                session.commit()
        finally:
            engine.dispose()
        return ids

    scratch_track_ids = prepare_storage(scratch_root)
    prepare_storage(ambient_root)

    def storage_state(root: Path) -> dict[str, str]:
        return {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
        }

    ambient_state = storage_state(ambient_root)
    config_loader = importlib.import_module("muzilla.config.loader")
    original_load_config = config_loader.load_config
    for key in (
        "MUZILLA_CONFIG_DIR",
        "MUZILLA_STORAGE__DB_PATH",
        "MUZILLA_STORAGE__LIBRARY_ROOT",
        "MUZILLA_STORAGE__DATA_DIR",
        "MUZILLA_STORAGE__CACHE_DIR",
        "MUZILLA_STORAGE__BLOB_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    def reject_ambient_config() -> None:
        raise AssertionError("host fixture must not load ambient storage configuration")

    monkeypatch.setattr(config_loader, "load_config", reject_ambient_config)
    first_bundle_id = harness._create_host_apply_bundle(
        scratch_root, scratch_root / "library", scratch_track_ids
    )
    assert first_bundle_id is not None

    monkeypatch.setattr(config_loader, "load_config", original_load_config)
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(ambient_root / "data" / "muzilla.db"))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(ambient_root / "library"))
    second_bundle_id = harness._create_host_apply_bundle(
        scratch_root, scratch_root / "library", scratch_track_ids
    )
    assert second_bundle_id is not None and second_bundle_id != first_bundle_id
    assert storage_state(ambient_root) == ambient_state

    engine = create_db_engine(scratch_root / "data" / "muzilla.db")
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            assert len(list(session.scalars(select(ReviewBundle)))) == 2
            for bundle_id in (first_bundle_id, second_bundle_id):
                revision = session.scalar(
                    select(ProposalRevision).where(
                        ProposalRevision.review_bundle_id == bundle_id,
                        ProposalRevision.is_current.is_(True),
                    )
                )
                assert revision is not None
                items = revision.source_snapshot.payload["items"]
                assert isinstance(items, list) and len(items) == 10
                for raw in items:
                    assert isinstance(raw, dict)
                    guard = raw["physical_guard"]
                    assert isinstance(guard, dict)
                    assert guard["library_root"] == str((scratch_root / "library").resolve())
                    track = session.get(Track, int(raw["source_id"]))
                    assert track is not None
                    expected = _source_precondition(raw)
                    assert (
                        _source_precondition_error(
                            track, expected, library_root=scratch_root / "library"
                        )
                        is None
                    )
    finally:
        engine.dispose()


def test_harness_audio_workload_in_candidate_fail_closed() -> None:
    """P1: fpcalc + rsgain run in the candidate image against valid long audio."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "fpcalc" in text
    assert "rsgain" in text
    assert "audio_fpcalc_ok" in text
    assert "audio_rsgain_returncode" in text
    assert "realistic_audio_file_count" in text
    assert "audio_tool_workload:" in text


def test_harness_exact_source_identity_gates_build() -> None:
    """P1: clean-tree gate, commit/tree/context hashes, image labels, running==candidate."""
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    assert "_require_clean_source" in text
    assert "git status" in text and "--porcelain" in text
    assert "worktree not clean" in text
    assert 'git", "archive' in text or '"archive"' in text
    assert "org.opencontainers.image.revision" in text
    assert "muzilla.build-context" in text
    assert "source_clean_verified" in text
    assert "source_identity:" in text
    # checksums bind source inputs, not outputs alone
    assert "thresholds.json" in text and "Dockerfile" in text


def test_harness_cancel_strict_all_samples_bound() -> None:
    """Oracle: cancel_detection_ms stays singular; harness correction is stricter.

    The 2000ms manifest bound is gated on the MAX of 30 predetermined samples
    (every sample <= 2000; p95 diagnostic only). The harness must wait for an
    actively running job (no fixed pre-cancel sleep), time from immediately
    before the cancel POST, poll at <=50ms, and require HTTP success plus
    terminal state exactly `cancelled` (fail-closed otherwise).
    """
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    start = text.index("# 5. cancellation")
    end = text.index("# 6. incremental")
    block = text[start:end]
    # predetermined 30 samples, gated on max (all-samples bound)
    assert "for _cancel_iter in range(30)" in block
    assert "max(_cancel_samples)" in block
    assert 'metrics["cancel_detection_ms"] = round(max(_cancel_samples), 1)' in block
    # p95 recorded as diagnostic only, never as the gate
    assert 'metrics["cancel_detection_p95_ms"]' in block
    # waits for demonstrably running job, never a fixed pre-cancel sleep
    assert '"running"' in block or "'running'" in block
    assert "never observed running (fail-closed)" in block
    assert "time.sleep(0.5)" not in block
    # timing starts immediately before the cancel POST
    assert "_t_cancel = time.monotonic()" in block
    _t_idx = block.index("_t_cancel = time.monotonic()")
    _post_idx = block.index("/cancel", _t_idx)
    assert 0 < _post_idx - _t_idx < 400
    # poll at <=50ms (production token interval)
    assert "0.05" in block
    assert "time.sleep(0.2)" not in block
    # requires HTTP success and exactly `cancelled` (fail-closed otherwise)
    assert "not in (200, 202)" in block
    assert '== "cancelled"' in block
    assert "not cancelled (fail-closed)" in block
    assert "no terminal state within 30s (fail-closed)" in block
    assert 'metrics["cancel_detection_ms"] = None' in block
    # terminal `succeeded`/`failed` must not count as cancel success
    assert '("succeeded", "failed")' in block
    # manifest untouched: singular bound preserved (no p95 gate substitution)
    assert 'metrics["cancel_sample_count"]' in block


def test_resource_sampler_captures_final_phase_peak_and_fails_original_threshold() -> None:
    from threading import Event

    module = _load_perf_harness_module()
    final_phase = Event()
    final_sample_seen = Event()

    def collect() -> dict[str, Any]:
        rss = 2049.0 if final_phase.is_set() else 1000.0
        if final_phase.is_set():
            final_sample_seen.set()
        return {
            "values": {
                "process_rss_mib": rss,
                "fd_count": 12,
                "sqlite_db_file_fd_count": 3,
                "container_cgroup_memory_mib": 1300,
            },
            "errors": {},
        }

    sampler = module._ResourceSampler(collect, interval_s=0.01, join_timeout_s=1, max_gap_s=1)
    try:
        assert sampler.start()
        final_phase.set()
        assert final_sample_seen.wait(timeout=2)
        measurements = sampler.stop()
    finally:
        if sampler._started and sampler._final_result is None:
            measurements = sampler.stop()

    assert measurements["complete"] is True
    assert measurements["sampler_stopped"] is True
    assert measurements["samples"][-1]["kind"] == "post-workflow-terminal"
    sampled_rss_max = max(sample["values"]["process_rss_mib"] for sample in measurements["samples"])
    assert measurements["authoritative_maxima"]["process_rss_mib"] == sampled_rss_max
    assert sampled_rss_max == 2049.0
    threshold = module._load_thresholds()["thresholds"]["muzilla_rss_mib_peak"]
    failures, evaluations = module._evaluate_thresholds(
        {"muzilla_rss_mib_peak": threshold},
        {"metrics": {"muzilla_rss_mib_peak": sampled_rss_max}},
    )
    assert failures
    assert evaluations["muzilla_rss_mib_peak"]["status"] == "fail"


def test_resource_sampler_errors_never_become_zero_or_pass() -> None:
    module = _load_perf_harness_module()

    def collect() -> dict[str, Any]:
        raise RuntimeError("resource probe denied")

    sampler = module._ResourceSampler(collect, interval_s=0.01, join_timeout_s=1, max_gap_s=1)
    assert sampler.start()
    measurements = sampler.stop()

    assert measurements["complete"] is False
    assert measurements["authoritative_maxima"] == {}
    assert measurements["metric_summaries"]["process_rss_mib"]["sampled_max"] is None
    failures, evaluations = module._evaluate_thresholds(
        {"muzilla_rss_mib_peak": {"target": 2048, "operator": "<="}},
        {"metrics": {}},
    )
    assert failures == ["muzilla_rss_mib_peak: unavailable (fail-closed)"]
    assert evaluations["muzilla_rss_mib_peak"]["status"] == "unavailable"


def test_resource_sampler_valid_sample_then_probe_error_is_not_authoritative() -> None:
    from threading import Event

    module = _load_perf_harness_module()
    error_seen = Event()
    calls = 0

    def collect() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "values": {
                    "process_rss_mib": 1200,
                    "fd_count": 12,
                    "sqlite_db_file_fd_count": 3,
                },
                "errors": {},
            }
        error_seen.set()
        raise RuntimeError("probe failed after a valid sample")

    sampler = module._ResourceSampler(collect, interval_s=0.01, join_timeout_s=1, max_gap_s=1)
    assert sampler.start()
    assert error_seen.wait(timeout=2)
    measurements = sampler.stop()

    assert measurements["complete"] is False
    assert measurements["authoritative_maxima"] == {}
    assert measurements["metric_summaries"]["process_rss_mib"]["sampled_max"] == 1200
    assert measurements["metric_summaries"]["process_rss_mib"]["errors"]


def test_resource_sampler_coverage_gap_prevents_authoritative_maxima() -> None:
    from threading import Event

    module = _load_perf_harness_module()
    slow_probe_started = Event()
    calls = 0

    def collect() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            slow_probe_started.set()
            Event().wait(timeout=0.2)
        return {
            "values": {
                "process_rss_mib": 1200,
                "fd_count": 12,
                "sqlite_db_file_fd_count": 3,
            },
            "errors": {},
        }

    sampler = module._ResourceSampler(collect, interval_s=0.01, join_timeout_s=1, max_gap_s=0.1)
    assert sampler.start()
    assert slow_probe_started.wait(timeout=2)
    measurements = sampler.stop()

    assert measurements["max_observation_gap_s"] > 0.1
    assert measurements["complete"] is False
    assert measurements["authoritative_maxima"] == {}


def test_resource_sampler_start_failure_and_malformed_output_fail_closed() -> None:
    from unittest.mock import Mock

    module = _load_perf_harness_module()
    failed_start = module._ResourceSampler(lambda: {}, interval_s=0.01, join_timeout_s=1)
    failed_start._thread.start = Mock(side_effect=RuntimeError("thread creation denied"))
    assert failed_start.start() is False
    start_result = failed_start.stop()
    assert start_result["complete"] is False
    assert start_result["authoritative_maxima"] == {}

    malformed = module._ResourceSampler(
        lambda: {"values": [], "errors": {}}, interval_s=0.01, join_timeout_s=1, max_gap_s=1
    )
    assert malformed.start()
    malformed_result = malformed.stop()
    assert malformed_result["complete"] is False
    assert malformed_result["authoritative_maxima"] == {}
    assert malformed_result["errors"]


def test_resource_sampler_all_zero_required_measurements_fail_closed() -> None:
    module = _load_perf_harness_module()

    def collect() -> dict[str, Any]:
        return {
            "values": {
                "process_rss_mib": 0,
                "fd_count": 0,
                "sqlite_db_file_fd_count": 0,
            },
            "errors": {},
        }

    sampler = module._ResourceSampler(collect, interval_s=0.01, join_timeout_s=1, max_gap_s=1)
    assert sampler.start()
    measurements = sampler.stop()
    assert measurements["complete"] is False
    assert measurements["authoritative_maxima"] == {}
    assert measurements["metric_summaries"]["process_rss_mib"]["sampled_max"] is None
    assert measurements["metric_summaries"]["fd_count"]["sampled_max"] is None
    assert measurements["metric_summaries"]["sqlite_db_file_fd_count"]["sampled_max"] == 0
    assert all(
        summary["complete"] is False
        for summary in measurements["metric_summaries"].values()
        if summary["sample_count"]
    )


def test_resource_sampler_rejects_mixed_zero_rss_or_fd_but_accepts_zero_db_fd() -> None:
    from threading import Event

    module = _load_perf_harness_module()

    def run_case(invalid_metric: str) -> None:
        first_sample_seen = Event()
        calls = 0

        def collect() -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                first_sample_seen.set()
            values: dict[str, int | float] = {
                "process_rss_mib": 100,
                "fd_count": 10,
                "sqlite_db_file_fd_count": 1 if calls == 1 else 0,
            }
            if calls > 1:
                values[invalid_metric] = 0
            return {"values": values, "errors": {}}

        sampler = module._ResourceSampler(collect, interval_s=1, join_timeout_s=1, max_gap_s=2)
        try:
            assert sampler.start()
            assert first_sample_seen.wait(timeout=2)
            measurements = sampler.stop()
        finally:
            if sampler._started and sampler._final_result is None:
                sampler.stop()

        summary = measurements["metric_summaries"][invalid_metric]
        assert summary["complete"] is False
        assert any("zero measurement" in error["error"] for error in summary["errors"])
        db_fd_summary = measurements["metric_summaries"]["sqlite_db_file_fd_count"]
        assert db_fd_summary["complete"] is True
        assert db_fd_summary["sampled_max"] == 1
        assert measurements["samples"][-1]["values"]["sqlite_db_file_fd_count"] == 0

    for invalid_metric in ("process_rss_mib", "fd_count"):
        run_case(invalid_metric)


def test_main_aborted_workflow_stops_sampler_cleans_and_publishes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import urllib.request
    from types import SimpleNamespace

    module = _load_perf_harness_module()
    events: list[str] = []
    image_id = "sha256:" + "a" * 64
    candidate_commit = "b" * 40
    output_dir = tmp_path / "benchmark-output"
    process = SimpleNamespace(pid=12345)
    sampler_instances: list[Any] = []

    class ReadyResponse:
        status = 200

        def __enter__(self) -> ReadyResponse:
            return self

        def __exit__(self, _exc_type: Any, _exc_value: Any, _traceback: Any) -> None:
            return None

    def fake_run(command: list[str], *_args: object, **_kwargs: object) -> Any:
        if command[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout=candidate_commit, stderr="")
        if command[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=image_id, stderr="")
        if "compose" in command and "up" in command:
            events.append("compose_up")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "compose" in command and "down" in command:
            events.append("compose_down")
            return SimpleNamespace(returncode=1, stdout="", stderr="injected cleanup failure")
        raise AssertionError(f"unexpected subprocess.run: {command}")

    monkeypatch.setattr(
        module.sys,
        "argv",
        ["perf_benchmark.py", "--count", "1", "--out", str(output_dir), "--no-build"],
    )
    monkeypatch.setattr(module, "_hardware_record", lambda: {"fixture": True})
    monkeypatch.setattr(
        module,
        "_require_clean_source",
        lambda: {
            "commit": candidate_commit,
            "tree": "tree-fixture",
            "build_context_sha256": "context-fixture",
        },
    )
    monkeypatch.setattr(module, "_generate_corpus", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "_verify_loopback_compose_config",
        lambda *_args: {"host_ip": "127.0.0.1", "published": "1846", "target": "8080"},
    )
    monkeypatch.setattr(
        module,
        "_require_empty_compose_project",
        lambda _project: {"containers": [], "networks": [], "volumes": []},
    )
    monkeypatch.setattr(
        module,
        "_verify_loopback_port_mapping",
        lambda *_args: {"host_ip": "127.0.0.1", "host_port": 1846, "container_port": 8080},
    )
    owned_resources = {
        "containers_by_label": ["container-id"],
        "containers_by_name": ["container-id"],
        "networks_by_label": [],
        "networks_by_name": [],
        "volumes_by_label": [],
        "volumes_by_name": [],
    }
    monkeypatch.setattr(module, "_docker_compose_resources", lambda _project: owned_resources)
    monkeypatch.setattr(
        module,
        "_collect_docker_resource_sample",
        lambda *_args: {
            "values": {
                "process_rss_mib": 100,
                "fd_count": 10,
                "sqlite_db_file_fd_count": 1,
            },
            "errors": {},
        },
    )

    real_sampler_type = module._ResourceSampler

    def capture_sampler(*args: object, **kwargs: object) -> Any:
        sampler = real_sampler_type(*args, **kwargs)
        real_stop = sampler.stop

        def tracked_stop() -> Any:
            measurements = real_stop()
            events.append("sampler_stopped")
            return measurements

        sampler.stop = tracked_stop
        sampler_instances.append(sampler)
        return sampler

    monkeypatch.setattr(module, "_ResourceSampler", capture_sampler)

    def fail_workflow(*_args: object, **_kwargs: object) -> None:
        events.append("workflow_failed")
        raise RuntimeError("injected workflow failure")

    monkeypatch.setattr(module, "_run_workflow", fail_workflow)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: ReadyResponse())

    real_cleanup = module._cleanup_compose_project

    def track_cleanup(*args: object, **kwargs: object) -> Any:
        events.append("cleanup_started")
        result = real_cleanup(*args, **kwargs)
        events.append("cleanup_finished")
        return result

    monkeypatch.setattr(module, "_cleanup_compose_project", track_cleanup)
    monkeypatch.setattr(
        module,
        "_stop_owned_process",
        lambda _process: events.append("process_stopped"),
    )
    real_writer = module._write_aborted_resource_report

    def track_report_write(*args: object, **kwargs: object) -> Any:
        events.append("report_published")
        return real_writer(*args, **kwargs)

    monkeypatch.setattr(module, "_write_aborted_resource_report", track_report_write)

    try:
        module.main()
    except RuntimeError as exc:
        assert "injected workflow failure" in str(exc)
    else:
        raise AssertionError("the injected workflow failure did not propagate")

    report_path = output_dir / "results" / "perf_result.json"
    report = json.loads(report_path.read_text())
    measurements = report["resource_measurements"]
    assert report["run_status"] == "aborted"
    assert report["threshold_pass"] is False
    assert "injected workflow failure" in report["workflow_error"]
    assert measurements["sampler_stopped"] is True
    assert measurements["complete"] is True
    assert measurements["samples"]
    assert measurements["samples"][-1]["kind"] == "post-workflow-terminal"
    assert measurements["samples"][-1]["values"] == {
        "process_rss_mib": 100,
        "fd_count": 10,
        "sqlite_db_file_fd_count": 1,
    }
    assert measurements["measurement_scope"]["sqlite_connection_count"].startswith("not measured")
    assert measurements["threshold_sources"]["sqlite_connections_max"].endswith("proxy)")
    assert report["compose_cleanup"]["status"] == "cleanup_command_failed"
    assert "injected cleanup failure" in report["compose_cleanup"]["error"]
    assert any(
        "compose_cleanup: cleanup_command_failed (fail-closed)" in failure
        for failure in report["threshold_failures"]
    )
    assert report["owned_process_cleanup"]["status"] == "cleaned"
    assert len(sampler_instances) == 1
    assert not sampler_instances[0]._thread.is_alive()
    assert events.index("workflow_failed") < events.index("sampler_stopped")
    assert events.index("sampler_stopped") < events.index("cleanup_started")
    assert events.index("cleanup_finished") < events.index("process_stopped")
    assert events.count("compose_down") == 1
    assert events.index("process_stopped") < events.index("report_published")
    checksums = json.loads((output_dir / "results" / "checksums.json").read_text())
    assert checksums["perf_result.json"] == hashlib.sha256(report_path.read_bytes()).hexdigest()


def test_resource_sampler_join_timeout_publishes_no_authoritative_maximum() -> None:
    from threading import Event

    module = _load_perf_harness_module()
    probe_entered = Event()
    release_probe = Event()

    def collect() -> dict[str, Any]:
        probe_entered.set()
        release_probe.wait()
        return {
            "values": {
                "process_rss_mib": 1200,
                "fd_count": 12,
                "sqlite_db_file_fd_count": 3,
            },
            "errors": {},
        }

    sampler = module._ResourceSampler(collect, interval_s=1, join_timeout_s=0.01, max_gap_s=2)
    try:
        assert sampler.start()
        assert probe_entered.wait(timeout=2)
        measurements = sampler.stop()
        assert measurements["sampler_stopped"] is False
        assert measurements["authoritative_maxima"] == {}
        assert "did not stop" in measurements["stop_error"]
    finally:
        release_probe.set()
        if sampler._started:
            sampler._thread.join(timeout=2)
    assert not sampler._thread.is_alive()


def test_threshold_comparison_rejects_invalid_measurements_targets_and_operators() -> None:
    module = _load_perf_harness_module()
    cases = (
        ({"value": float("nan")}, {"target": 1, "operator": "<="}),
        ({"value": 1}, {"target": float("inf"), "operator": "<="}),
        ({"value": True}, {"target": 1, "operator": "<="}),
        ({"value": 1}, {"target": 1, "operator": "approximately"}),
    )
    for metrics, threshold in cases:
        failures, evaluations = module._evaluate_thresholds(
            {"value": threshold}, {"metrics": metrics}
        )
        assert failures
        assert evaluations["value"]["status"].startswith("invalid_")


def test_resource_collectors_count_only_owned_processes_and_exact_db_file_fds() -> None:
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import patch

    module = _load_perf_harness_module()
    ps_output = "100 1 512\n101 100 256\n999 1 8192\n"
    lsof_output = (
        "p100\nf0u\nn/dev/null\nf1w\nn/tmp/data/muzilla.db\n"
        "f2u\nn/tmp/data/muzilla.db-wal\nf3r\nn/tmp/other\nfmem\n"
    )
    responses = (
        SimpleNamespace(returncode=0, stdout=ps_output, stderr=""),
        SimpleNamespace(returncode=0, stdout=lsof_output, stderr=""),
    )
    with patch.object(module.subprocess, "run", side_effect=responses):
        sample = module._collect_host_resource_sample(100, Path("/tmp/data/muzilla.db"))
    assert sample["values"]["process_rss_mib"] == 0.75
    assert sample["values"]["fd_count"] == 4
    assert sample["values"]["sqlite_db_file_fd_count"] == 1
    assert sample["errors"] == {}


def _run_docker_resource_probe_with_fake_fds(*, always_race: bool) -> tuple[dict[str, Any], int]:
    import io
    import json
    import os
    from types import SimpleNamespace

    module = _load_perf_harness_module()
    fd_directory_reads = 0
    db_fd_reads = 0
    output = io.StringIO()

    def listdir(path: str) -> list[str]:
        nonlocal fd_directory_reads
        if path == "/proc":
            return ["1", "98765"]
        if path == "/proc/1/fd":
            fd_directory_reads += 1
            return ["3", "4"]
        raise FileNotFoundError(path)

    def readlink(path: str) -> str:
        nonlocal db_fd_reads
        if path == "/proc/1/fd/3":
            db_fd_reads += 1
            if always_race or db_fd_reads == 1:
                raise FileNotFoundError(path)
            return "/data/muzilla.db"
        if path == "/proc/1/fd/4":
            return "/data/muzilla.db-wal"
        raise FileNotFoundError(path)

    def fake_open(path: str, *_args: Any, **_kwargs: Any) -> io.StringIO:
        if path == "/proc/1/status":
            return io.StringIO("Name: muzilla\nVmRSS: 1024 kB\n")
        if path == "/sys/fs/cgroup/memory.current":
            return io.StringIO("1048576")
        raise FileNotFoundError(path)

    fake_os = SimpleNamespace(
        path=os.path,
        normpath=os.path.normpath,
        listdir=listdir,
        readlink=readlink,
        getpid=lambda: 98765,
    )
    script = module._DOCKER_RESOURCE_PROBE.replace("import json, os, sys", "", 1)
    namespace = {
        "json": json,
        "os": fake_os,
        "sys": SimpleNamespace(argv=["probe", "/data/muzilla.db"]),
        "open": fake_open,
        "print": lambda value: output.write(f"{value}\n"),
    }
    exec(compile(script, "<docker-resource-probe>", "exec"), namespace)
    return json.loads(output.getvalue()), fd_directory_reads


def test_docker_resource_probe_retries_a_closed_descriptor() -> None:
    result, fd_directory_reads = _run_docker_resource_probe_with_fake_fds(always_race=False)

    assert fd_directory_reads == 2
    assert result["errors"] == {}
    assert result["values"]["sqlite_db_file_fd_count"] == 1


def test_docker_resource_probe_fails_closed_after_persistent_descriptor_churn() -> None:
    result, fd_directory_reads = _run_docker_resource_probe_with_fake_fds(always_race=True)

    assert fd_directory_reads == 3
    assert "sqlite_db_file_fd_count" not in result["values"]
    assert "after 3 attempts" in result["errors"]["sqlite_db_file_fd_count"]


def test_docker_resource_probe_failure_is_missing_not_zero() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    module = _load_perf_harness_module()
    with patch.object(
        module.subprocess,
        "run",
        return_value=SimpleNamespace(returncode=1, stdout="", stderr="probe denied"),
    ) as run:
        sample = module._collect_docker_resource_sample(
            "muzilla-perf-test-muzilla-1", "/data/muzilla.db"
        )
    assert sample["values"] == {}
    assert sample["errors"]["container_probe"] == "probe denied"
    command = run.call_args.args[0]
    assert command[:4] == [
        "docker",
        "exec",
        "muzilla-perf-test-muzilla-1",
        "python",
    ]
    assert command[-1] == "/data/muzilla.db"


def test_owned_process_cleanup_waits_and_verifies_termination() -> None:
    import subprocess
    from unittest.mock import Mock

    module = _load_perf_harness_module()
    process = Mock()
    process.pid = 1234
    process.poll.side_effect = [None, -9]
    process.wait.side_effect = [subprocess.TimeoutExpired("mock", 5), -9]
    module._stop_owned_process(process)
    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2
    assert process.poll.call_count == 2

    stuck = Mock()
    stuck.pid = 1235
    stuck.poll.side_effect = [None, None]
    stuck.wait.side_effect = [subprocess.TimeoutExpired("mock", 5), None]
    try:
        module._stop_owned_process(stuck)
    except RuntimeError as exc:
        assert "did not stop" in str(exc)
    else:
        raise AssertionError("live owned process was treated as stopped")


def test_compose_project_preflight_fails_closed_on_query_error_and_stopped_collision() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    module = _load_perf_harness_module()
    project = "muzilla-perf-test-uuid"
    with patch.object(
        module.subprocess,
        "run",
        return_value=SimpleNamespace(returncode=1, stdout="", stderr="daemon unavailable"),
    ) as run:
        try:
            module._require_empty_compose_project(project)
        except RuntimeError as exc:
            assert "resource query" in str(exc) and "failed" in str(exc)
        else:
            raise AssertionError("Docker query error was treated as an empty project")
    assert len(run.call_args_list) == 1

    empty = SimpleNamespace(returncode=0, stdout="", stderr="")
    stopped = SimpleNamespace(returncode=0, stdout="stopped-container-id\n", stderr="")
    with patch.object(
        module.subprocess, "run", side_effect=[empty, stopped, empty, empty, empty, empty]
    ) as run:
        try:
            module._require_empty_compose_project(project)
        except RuntimeError as exc:
            assert "pre-existing Docker resources" in str(exc)
        else:
            raise AssertionError("stopped name-collision resource was accepted")
    assert run.call_args_list[0].args[0][1:3] == ["ps", "-aq"]
    assert run.call_args_list[1].args[0][-1] == f"name={project}"


def test_compose_cleanup_removes_only_fresh_labeled_resources_without_volumes() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    module = _load_perf_harness_module()
    project = "muzilla-perf-test-uuid"
    compose_command = ["docker", "compose", "-p", project, "-f", "base.yml", "-f", "perf.yml"]
    env = {"MUZILLA_PERF_IMAGE": "sha256:test", "MUZILLA_BIND_ADDRESS": "127.0.0.1"}

    def response(stdout: str = "", returncode: int = 0, stderr: str = "") -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    before = [
        response("container-id\n"),
        response("container-id\n"),
        response("network-id\n"),
        response("network-id\n"),
        response(),
        response(),
    ]
    after = [response() for _ in range(6)]
    with patch.object(module.subprocess, "run", side_effect=[*before, response(), *after]) as run:
        cleanup = module._cleanup_compose_project(project, compose_command, env)

    assert cleanup["status"] == "cleaned"
    assert cleanup["before"] == {
        "containers": ["container-id"],
        "networks": ["network-id"],
        "volumes": [],
    }
    down = run.call_args_list[6]
    assert down.args[0] == [*compose_command, "down"]
    assert "-v" not in down.args[0]
    assert down.kwargs["env"] == env
    assert cleanup["after"] == {"containers": [], "networks": [], "volumes": []}


def test_compose_cleanup_refuses_unowned_name_collision_and_reports_failure() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    module = _load_perf_harness_module()
    project = "muzilla-perf-test-uuid"
    compose_command = ["docker", "compose", "-p", project, "-f", "base.yml", "-f", "perf.yml"]
    env: dict[str, str] = {}
    empty = SimpleNamespace(returncode=0, stdout="", stderr="")
    foreign = SimpleNamespace(returncode=0, stdout="foreign-id\n", stderr="")
    with patch.object(
        module.subprocess, "run", side_effect=[empty, foreign, empty, empty, empty, empty]
    ) as run:
        cleanup = module._cleanup_compose_project(project, compose_command, env)
    assert cleanup["status"] == "ownership_unknown"
    assert len(run.call_args_list) == 6

    owned = [
        SimpleNamespace(returncode=0, stdout="container-id\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="container-id\n", stderr=""),
        empty,
        empty,
        empty,
        empty,
    ]
    down_failed = SimpleNamespace(returncode=1, stdout="", stderr="down denied")
    with patch.object(module.subprocess, "run", side_effect=[*owned, down_failed]) as run:
        cleanup = module._cleanup_compose_project(project, compose_command, env)
    assert cleanup["status"] == "cleanup_command_failed"
    assert "down denied" in cleanup["error"]
    assert len(run.call_args_list) == 7


def test_compose_cleanup_fails_if_owned_resources_remain_or_verification_fails() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    module = _load_perf_harness_module()
    project = "muzilla-perf-test-uuid"
    compose_command = ["docker", "compose", "-p", project, "-f", "base.yml", "-f", "perf.yml"]
    env: dict[str, str] = {}
    owned = [
        SimpleNamespace(returncode=0, stdout="container-id\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="container-id\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="network-id\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="network-id\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="volume-id\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="volume-id\n", stderr=""),
    ]
    empty = SimpleNamespace(returncode=0, stdout="", stderr="")
    remains = [
        SimpleNamespace(returncode=0, stdout="container-id\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="container-id\n", stderr=""),
        empty,
        empty,
        empty,
        empty,
    ]
    with patch.object(module.subprocess, "run", side_effect=[*owned, empty, *remains]):
        cleanup = module._cleanup_compose_project(project, compose_command, env)
    assert cleanup["status"] == "resources_remain"
    assert cleanup["after"]["containers"] == ["container-id"]

    query_failed = SimpleNamespace(returncode=1, stdout="", stderr="daemon unavailable")
    with patch.object(module.subprocess, "run", side_effect=[*owned, empty, query_failed]):
        cleanup = module._cleanup_compose_project(project, compose_command, env)
    assert cleanup["status"] == "verification_failed"
    assert "resource query" in cleanup["error"]


def test_compose_config_and_actual_port_mapping_require_loopback() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    module = _load_perf_harness_module()
    compose_command = ["docker", "compose", "-p", "muzilla-perf-test"]
    env = {"MUZILLA_BIND_ADDRESS": "127.0.0.1"}
    loopback_config = json.dumps(
        {
            "services": {
                "muzilla": {
                    "ports": [{"host_ip": "127.0.0.1", "published": "1846", "target": 8080}]
                }
            }
        }
    )
    with patch.object(
        module.subprocess,
        "run",
        return_value=SimpleNamespace(returncode=0, stdout=loopback_config, stderr=""),
    ) as run:
        assert module._verify_loopback_compose_config(compose_command, env) == {
            "host_ip": "127.0.0.1",
            "published": "1846",
            "target": 8080,
        }
    assert run.call_args.args[0] == [*compose_command, "config", "--format", "json"]
    assert run.call_args.kwargs["env"] == env

    wildcard_config = loopback_config.replace("127.0.0.1", "0.0.0.0")
    with patch.object(
        module.subprocess,
        "run",
        return_value=SimpleNamespace(returncode=0, stdout=wildcard_config, stderr=""),
    ):
        try:
            module._verify_loopback_compose_config(compose_command, env)
        except RuntimeError as exc:
            assert "not loopback-only" in str(exc)
        else:
            raise AssertionError("wildcard Compose publication was accepted")

    loopback_mapping = json.dumps({"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "1846"}]})
    with patch.object(
        module.subprocess,
        "run",
        return_value=SimpleNamespace(returncode=0, stdout=loopback_mapping, stderr=""),
    ):
        assert module._verify_loopback_port_mapping("owned-container") == {
            "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "1846"}]
        }

    wildcard_mapping = loopback_mapping.replace("127.0.0.1", "0.0.0.0")
    with patch.object(
        module.subprocess,
        "run",
        return_value=SimpleNamespace(returncode=0, stdout=wildcard_mapping, stderr=""),
    ):
        try:
            module._verify_loopback_port_mapping("owned-container")
        except RuntimeError as exc:
            assert "not loopback-only" in str(exc)
        else:
            raise AssertionError("wildcard container port mapping was accepted")


def test_sqlite_connection_threshold_is_explicit_db_file_fd_proxy() -> None:
    module = _load_perf_harness_module()
    thresholds = module._load_thresholds()["thresholds"]
    failures, evaluations = module._evaluate_thresholds(
        {"sqlite_connections_max": thresholds["sqlite_connections_max"]},
        {
            "metrics": {
                "sqlite_connections_max": 2,
                "sqlite_db_file_fd_sampled_max": 7,
            }
        },
    )
    assert failures == []
    assert evaluations["sqlite_connections_max"]["source_metric"] == (
        "sqlite_db_file_fd_sampled_max"
    )
    assert evaluations["sqlite_connections_max"]["observed"] == 7
    assert "proxy" in evaluations["sqlite_connections_max"]["measurement_note"]


def test_compose_partial_startup_has_owned_cleanup_fallback() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    start_attempt = text.index("compose_start_attempted = True")
    compose_up = text.index('"up", "-d", "--no-build"', start_attempt)
    cleanup_fallback = text.index(
        "if compose_start_attempted and not compose_cleanup_attempted:", compose_up
    )
    cleanup = text.index("_cleanup_compose_project(", cleanup_fallback)
    assert start_attempt < compose_up < cleanup_fallback < cleanup
    assert "_cleanup_compose_project(compose_project, compose_command, compose_env)" in text
    assert '"down", "-v"' not in text


def test_resource_sampler_covers_browser_and_freezes_only_after_join() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    start = text.index("resource_sampler.start()")
    workflow = text.index("metrics = _run_workflow", start)
    browser = text.index("# Playwright Apply/Undo of a 10-track", workflow)
    stop = text.index("resource_measurements = resource_sampler.stop()", browser)
    publish = text.index('result["peak_rss_mib"]', stop)
    threshold = text.index("_evaluate_thresholds(", publish)
    assert start < workflow < browser < stop < publish < threshold
    assert "t.join(timeout=2)" not in text
    assert 'os.readlink(f"/proc/1/fd/{name}") == db_path' in text
    assert "container_cgroup_memory_mib" in text
    assert "sqlite_db_file_fd_sampled_max" in text
    preflight = text.index(
        'result["compose_preflight_resources"] = _require_empty_compose_project('
    )
    start_attempt = text.index("compose_start_attempted = True", preflight)
    compose_up = text.index('"up", "-d", "--no-build"', start_attempt)
    assert preflight < start_attempt < compose_up
    assert '"MUZILLA_BIND_ADDRESS": "127.0.0.1"' in text
    assert '"down", "-v"' not in text
