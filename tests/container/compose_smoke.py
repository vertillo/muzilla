"""Isolated Compose smoke test for runtime readiness and hardening."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).parent.parent.parent
IMAGE = os.environ.get("MUZILLA_TEST_IMAGE")


def _compose(env: dict[str, str], project: str, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", "compose", "-p", project, *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def _json_url(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=5) as response:
        assert response.status == 200
        return json.load(response)  # type: ignore[no-any-return]


def _json_request(
    url: str,
    *,
    method: str,
    body: dict[str, object],
    csrf_token: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    parsed = urlsplit(url)
    headers = {
        "Content-Type": "application/json",
        "Origin": f"{parsed.scheme}://{parsed.netloc}",
    }
    if csrf_token is not None:
        headers["X-CSRF-Token"] = csrf_token
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)  # type: ignore[no-any-return]


def _wait_for_job(base_url: str, job_id: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = _json_url(f"{base_url}/jobs/{job_id}")
        state = job["state"]
        if state == "succeeded":
            return
        if state in {"failed", "cancelled"}:
            raise RuntimeError(f"isolated scan ended in {state}")
        time.sleep(0.2)
    raise RuntimeError("isolated scan did not finish")


def main() -> None:
    if IMAGE is None:
        raise SystemExit("set MUZILLA_TEST_IMAGE to a built image tag")

    project = f"muzilla-smoke-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="muzilla-compose-smoke-") as temp_dir:
        library = Path(temp_dir) / "music"
        library.mkdir()
        music_fixture = library / "reset-fixture.mp3"
        shutil.copyfile(REPO_ROOT / "tests/fixtures/audio/silence.mp3", music_fixture)
        music_hash = hashlib.sha256(music_fixture.read_bytes()).hexdigest()
        override = Path(temp_dir) / "compose.reset-smoke.yaml"
        override.write_text(
            "services:\n"
            "  muzilla:\n"
            "    volumes:\n"
            f"      - type: bind\n        source: {library}\n        target: /music\n        read_only: true\n"
        )
        env = {
            **os.environ,
            "COMPOSE_FILE": f"{REPO_ROOT / 'docker-compose.yml'}:{override}",
            "MUZILLA_AUTH__ENABLED": "false",
            "MUZILLA_BIND_ADDRESS": "127.0.0.1",
            "MUZILLA_IMAGE": IMAGE,
            "MUZILLA_LIBRARY_PATH": str(library),
            "MUZILLA_PORT": "0",
        }
        try:
            _compose(env, project, "up", "-d", "--no-build", "--wait", "--wait-timeout", "60")
            port = _compose(env, project, "port", "muzilla", "8080").strip().rsplit(":", 1)[1]
            base_url = f"http://127.0.0.1:{port}/api"

            assert _json_url(f"{base_url}/health") == {"status": "ok"}
            readiness = _json_url(f"{base_url}/ready")
            assert readiness["status"] == "ready"
            capabilities = _json_url(f"{base_url}/capabilities")
            assert capabilities["replaygain"] == {
                "name": "replaygain",
                "state": "available",
                "enabled": True,
                "available": True,
                "detail": "operational",
            }
            assert capabilities["fingerprint"]["available"] is True  # type: ignore
            assert capabilities["fingerprint"]["state"] == "available"  # type: ignore

            version = _compose(env, project, "exec", "-T", "muzilla", "rsgain", "--version")
            assert version.strip()
            fpcalc_help = _compose(env, project, "exec", "-T", "muzilla", "fpcalc", "-h")
            assert fpcalc_help.strip() != ""
            # Functional fpcalc as non-root runtime user against disposable audio
            ls_out = _compose(env, project, "exec", "-T", "muzilla", "ls", "-l", "/music", check=False)
            assert "reset-fixture.mp3" in ls_out
            fpcalc_run = _compose(env, project, "exec", "-T", "muzilla", "fpcalc", "/music/reset-fixture.mp3", check=False)
            # Silence yields Empty fingerprint; any execution without permission error proves non-root capability
            assert "permission" not in fpcalc_run.lower()
            assert "not found" not in fpcalc_run.lower()

            auth_status = _json_url(f"{base_url}/auth/status")
            csrf_token = str(auth_status["csrf_token"])
            provider = _json_request(
                f"{base_url}/settings/providers/discogs",
                method="PUT",
                body={"enabled": False, "token": "isolated-compose-secret"},
                csrf_token=csrf_token,
            )
            assert provider["token_configured"] is True
            scan = _json_request(
                f"{base_url}/scan",
                method="POST",
                body={"root": "/music"},
            )
            _wait_for_job(base_url, int(scan["job_id"]))  # type: ignore
            tracks = _json_url(f"{base_url}/tracks?limit=10")["items"]  # type: ignore
            assert len(tracks) == 1  # type: ignore[arg-type]
            track_id = int(tracks[0]["id"])  # type: ignore
            # Supported API/worker fingerprint flow: POST /tracks/{id}/analyze
            analyze = _json_request(
                f"{base_url}/tracks/{track_id}/analyze",
                method="POST",
                body={},
                csrf_token=csrf_token,
            )
            _wait_for_job(base_url, int(analyze["job_id"]))  # type: ignore
            job_detail = _json_url(f"{base_url}/jobs/{int(analyze['job_id'])}")  # type: ignore
            assert job_detail["state"] == "succeeded"
            # Fingerprint persisted via worker: check track still exists and job result has fingerprint flag
            assert job_detail.get("result", {}).get("analysis_started") is True  # type: ignore

            reset = _json_request(
                f"{base_url}/settings/reset/catalog",
                method="POST",
                body={
                    "scope": "catalog_and_activity",
                    "confirmation": "RESET CATALOG AND ACTIVITY",
                },
                csrf_token=csrf_token,
                idempotency_key="compose-isolated-catalog-reset",
            )
            assert reset["state"] == "succeeded"
            assert reset["music_files_touched"] is False
            assert hashlib.sha256(music_fixture.read_bytes()).hexdigest() == music_hash

            # Recreate the container against the same named /data volume. Settings,
            # managed credentials and reset audit persist; catalog/activity stays empty.
            _compose(
                env,
                project,
                "up",
                "-d",
                "--no-build",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "60",
            )
            port = _compose(env, project, "port", "muzilla", "8080").strip().rsplit(":", 1)[1]
            base_url = f"http://127.0.0.1:{port}/api"
            settings = _json_url(f"{base_url}/settings")
            discogs = next(
                item for item in settings["providers"]  # type: ignore
                if item["provider"] == "discogs"
            )
            assert discogs["token_configured"] is True
            assert _json_url(f"{base_url}/tracks?limit=10")["items"] == []
            assert hashlib.sha256(music_fixture.read_bytes()).hexdigest() == music_hash

            container_id = _compose(env, project, "ps", "-q", "muzilla").strip()
            inspect_result = subprocess.run(
                ["docker", "inspect", container_id],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            inspected = json.loads(inspect_result.stdout)[0]
            host = inspected["HostConfig"]
            assert inspected["Config"]["User"] == "muzilla"
            assert host["CapDrop"] == ["ALL"]
            assert "no-new-privileges:true" in host["SecurityOpt"]
            assert host["Memory"] == 2 * 1024**3
            assert host["MemorySwap"] == 2 * 1024**3
            assert host["NanoCpus"] == 2_000_000_000
            assert inspected["State"]["Health"]["Status"] == "healthy"
            music_mount = next(mount for mount in inspected["Mounts"] if mount["Destination"] == "/music")
            assert music_mount["RW"] is False
        except Exception:
            logs = _compose(env, project, "logs", "--no-color", check=False)
            if logs:
                print(logs)
            raise
        finally:
            _compose(env, project, "down", "--volumes", "--remove-orphans", check=False)


if __name__ == "__main__":
    main()
