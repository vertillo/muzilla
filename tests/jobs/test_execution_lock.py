from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
from typing import Any

import pytest

from muzilla.jobs.execution_lock import JobExecutionLock


def _hold_execution_lock(directory: str, job_id: int, acquired: Any, release: Any) -> None:
    lock = JobExecutionLock.try_acquire(Path(directory), job_id)
    if lock is None:
        return
    acquired.set()
    release.wait()
    lock.close()


@pytest.mark.skipif(os.name != "posix", reason="job execution locks require POSIX flock")
def test_process_exit_releases_job_execution_lock(tmp_path: Path) -> None:
    directory = tmp_path / "job-locks"
    directory.mkdir(mode=0o700)
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_execution_lock,
        args=(str(directory), 37, acquired, release),
    )
    process.start()
    try:
        assert acquired.wait(timeout=5)
        assert JobExecutionLock.try_acquire(directory, 37) is None
    finally:
        process.terminate()
        process.join(timeout=5)
    assert not process.is_alive()

    recovered_lock = JobExecutionLock.try_acquire(directory, 37)
    assert recovered_lock is not None
    recovered_lock.close()
