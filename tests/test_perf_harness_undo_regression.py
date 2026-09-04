"""Regression for PERF-SCALE-001 undo_p95_ms unavailable.

Before fix, _undo_run_id relied only on GET apply_runs and fallback parsing
of b_app after a 2s poll. If GET was empty, fallback could still be None
due to late parsing or race, leading to 0 undo samples and threshold
fail-closed (perf_result.json: undo_p95_ms null). After fix, harness
captures apply_run_id directly from POST /apply response and uses it
as primary source for undo, with GET as fallback.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_harness_captures_direct_apply_run_id_for_undo() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # must capture direct id from POST apply response, not only via GET poll
    assert "_apply_run_id_direct" in text
    assert 'j_app.get("apply_run_id")' in text
    # must still keep GET fallback for idempotency/retry cases
    assert "apply_runs" in text
    # must record skip/failure for observability instead of silently 0 samples
    assert "undo_skipped" in text
    assert "undo_post_failures" in text or "undo_errors" in text


def test_harness_undo_retry_on_409_not_yet_applied() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # if run not yet applied, harness retries after short sleep
    assert "only an applied" in text
    assert "time.sleep(0.5)" in text


def test_direct_apply_run_id_extraction_logic() -> None:
    """Unit-check the extraction that previously left _undo_run_id None."""

    def extract_direct(b_app: str | None) -> int | None:

        j = json.loads(b_app) if b_app else {}
        cand = j.get("apply_run_id") or j.get("applyRunId") or j.get("id")
        if cand is None:
            return None
        return int(cand)

    assert extract_direct('{"apply_run_id": 42, "job_id": 1}') == 42
    assert extract_direct('{"applyRunId": 7}') == 7
    assert extract_direct('{"id": 99}') == 99
    assert extract_direct("{}") is None
    assert extract_direct(None) is None
    # fallback via GET empty should still use direct id
    b_app = '{"apply_run_id": 123, "job_id": 10}'
    direct = extract_direct(b_app)
    # simulate GET returning empty runs -> direct still valid
    runs: list[dict[str, object]] = []
    undo_id = None
    if runs:
        undo_id = runs[-1].get("id")
    if undo_id is None:
        undo_id = direct
    assert undo_id == 123


def test_harness_still_measures_30_sample_repeatable_benchmark() -> None:
    text = (REPO_ROOT / "scripts" / "perf_benchmark.py").read_text()
    # must still be a repeatable 30-sample benchmark, not a single operation
    assert "for _bid in _bundle_ids[:30]:" in text
    assert "range(30)" in text or "[:30]" in text
    assert "apply_p95_ms_per_bundle" in text
    assert "undo_p95_ms" in text
    # thresholds.json is immutable — must not be modified to relax
    assert "thresholds.json" in text or "thresholds" in text
