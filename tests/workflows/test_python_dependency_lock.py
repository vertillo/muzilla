"""Regression checks for locked Python installs and their hosted CI gates."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
UV_VERSION = "0.11.29"


def _workflow(name: str) -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text())
    return document


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def _assert_uv_version(job: dict[str, Any]) -> None:
    setup = next(
        step for step in job["steps"] if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert setup["with"]["version"] == UV_VERSION


def test_ci_python_environments_use_locked_job_specific_dependencies() -> None:
    ci = _workflow("ci.yml")
    expected_installs = {
        "backend": "uv sync --locked --python 3.12 --extra dev --extra audio",
        "frontend": "uv sync --locked --python 3.12 --no-dev --extra audio",
        "e2e": "uv sync --locked --python 3.12 --no-dev --extra audio",
    }

    assert {"backend", "frontend", "e2e", "docker"} <= set(ci["jobs"])
    for job_name, command in expected_installs.items():
        job = ci["jobs"][job_name]
        _assert_uv_version(job)
        install_name = (
            "Install dependencies" if job_name == "backend" else "Install backend dependencies"
        )
        assert _step(job, install_name)["run"] == command
        assert job.get("continue-on-error") is not True
        assert "if" not in job

    for job_name in ("e2e", "docker"):
        assert ci["jobs"][job_name]["needs"] == ["backend", "frontend"]

    for job in ci["jobs"].values():
        for step in job["steps"]:
            command = step.get("run", "")
            assert "uv pip install" not in command
            assert "pip install -e" not in command


def test_release_and_development_installs_are_locked() -> None:
    release = _workflow("release.yml")
    release_job = release["jobs"]["release"]
    _assert_uv_version(release_job)
    assert _step(release_job, "Install dependencies")["run"] == (
        "uv sync --locked --python 3.12 --extra dev"
    )

    assert (
        "uv sync --locked --python 3.12 --extra dev --extra audio"
        in (REPO_ROOT / "README.md").read_text()
    )
    assert (
        "uv sync --locked --python 3.12 --extra dev --extra audio"
        in (REPO_ROOT / "CONTRIBUTING.md").read_text()
    )
    assert (
        "uv sync --locked --all-extras"
        in (REPO_ROOT / "scripts" / "sandbox-entrypoint.sh").read_text()
    )
    assert (
        "uv sync --locked --all-extras" in (REPO_ROOT / "scripts" / "test-sandbox.sh").read_text()
    )


def test_docker_installs_and_records_the_locked_runtime_inventory() -> None:
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text()

    assert "COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /bin/uv" in dockerfile
    assert "COPY pyproject.toml README.md uv.lock ./" in dockerfile
    assert "uv sync --locked --no-dev --extra audio --no-editable" in dockerfile
    assert "uv sync --locked --no-dev --extra audio --no-editable --check" in dockerfile
    assert "uv export --locked --no-dev --extra audio --no-emit-project" in dockerfile
    assert "uv pip freeze --python /opt/venv/bin/python --exclude muzilla" in dockerfile
    assert "/usr/share/muzilla/python-runtime-lock.txt" in dockerfile
    assert "/usr/share/muzilla/python-runtime-inventory.txt" in dockerfile
    assert 'PATH="/opt/venv/bin:${PATH}"' in dockerfile
    assert "USER muzilla" in dockerfile
    assert "pip install" not in dockerfile


def test_ci_audits_the_built_image_runtime_inventory_and_required_jobs() -> None:
    ci = _workflow("ci.yml")
    jobs = ci["jobs"]
    docker = jobs["docker"]
    _assert_uv_version(docker)

    assert jobs["backend"].get("continue-on-error") is not True
    assert jobs["frontend"].get("continue-on-error") is not True
    assert jobs["e2e"].get("continue-on-error") is not True
    assert docker.get("continue-on-error") is not True

    audit = _step(docker, "Audit exact Python runtime inventory")
    audit_script = audit["run"]
    assert "docker run --rm --entrypoint cat muzilla:ci" in audit_script
    assert "/usr/share/muzilla/python-runtime-inventory.txt" in audit_script
    assert "uvx pip-audit -r" in audit_script
    assert "runtime-inventory.txt" in audit_script

    steps = docker["steps"]
    build_index = next(
        i for i, step in enumerate(steps) if step.get("name") == "Build image (no push)"
    )
    audit_index = steps.index(audit)
    native_index = next(
        i
        for i, step in enumerate(steps)
        if step.get("name") == "Verify native runtime in the built image"
    )
    assert build_index < native_index < audit_index


def test_locked_check_passes_here_and_rejects_stale_project_metadata(tmp_path: Path) -> None:
    checked_in = subprocess.run(
        ["uv", "lock", "--check"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert checked_in.returncode == 0, checked_in.stdout + checked_in.stderr

    shutil.copy2(REPO_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy2(REPO_ROOT / "uv.lock", tmp_path / "uv.lock")
    shutil.copy2(REPO_ROOT / "README.md", tmp_path / "README.md")
    version_source = tmp_path / "src" / "muzilla" / "__about__.py"
    version_source.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "src" / "muzilla" / "__about__.py", version_source)
    original_lock_digest = hashlib.sha256((tmp_path / "uv.lock").read_bytes()).hexdigest()
    pyproject = tmp_path / "pyproject.toml"
    contents = pyproject.read_text()
    assert '"fastapi>=0.115"' in contents
    pyproject.write_text(contents.replace('"fastapi>=0.115"', '"fastapi>=0.141.0"', 1))

    stale = subprocess.run(
        ["uv", "lock", "--check"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    diagnostic = stale.stdout + stale.stderr
    assert stale.returncode != 0
    assert "lockfile" in diagnostic.lower()
    assert "needs to be updated" in diagnostic.lower()
    assert hashlib.sha256((tmp_path / "uv.lock").read_bytes()).hexdigest() == original_lock_digest
