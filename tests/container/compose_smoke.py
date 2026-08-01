"""Isolated Compose smoke test for runtime readiness and hardening."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path

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


def main() -> None:
    if IMAGE is None:
        raise SystemExit("set MUZILLA_TEST_IMAGE to a built image tag")

    project = f"muzilla-smoke-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="muzilla-compose-smoke-") as temp_dir:
        library = Path(temp_dir) / "music"
        library.mkdir()
        env = {
            **os.environ,
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

            version = _compose(env, project, "exec", "-T", "muzilla", "rsgain", "--version")
            assert version.strip()

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
        except Exception:
            logs = _compose(env, project, "logs", "--no-color", check=False)
            if logs:
                print(logs)
            raise
        finally:
            _compose(env, project, "down", "--volumes", "--remove-orphans", check=False)


if __name__ == "__main__":
    main()
