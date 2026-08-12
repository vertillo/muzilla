"""Isolated Docker-volume backup/restore smoke for a release candidate.

This is deliberately operator-workflow test machinery, not a product backup
command. It archives a disposable ``/data`` named volume on the host and
restores it only into another empty disposable volume. The music fixture is a
read-only bind mount throughout, so its checksum also guards the boundary.
"""

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

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE = os.environ.get("MUZILLA_TEST_IMAGE")
_HELPER_IMAGE = "alpine:3.21"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stdout + result.stderr)
    return result


def _compose(env: dict[str, str], project: str, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(REPO_ROOT / "docker-compose.yml"),
            "-f",
            env["MUZILLA_SMOKE_OVERRIDE"],
            *args,
        ],
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _volume_fingerprint(volume: str) -> str:
    result = _docker(
        "run",
        "--rm",
        "--mount",
        f"type=volume,src={volume},dst=/data,readonly",
        _HELPER_IMAGE,
        "sh",
        "-ec",
        # Directory mtimes are legitimately changed by extraction, so this
        # manifest proves restored file paths and bytes rather than comparing
        # an unstable tar stream.
        "cd /data && find . -type f -print | LC_ALL=C sort | "
        "while IFS= read -r path; do sha256sum \"$path\"; done | "
        "sha256sum | cut -d ' ' -f 1",
    )
    fingerprint = result.stdout.strip()
    if len(fingerprint) != 64:
        raise RuntimeError(f"unexpected volume checksum output: {result.stdout!r}")
    return fingerprint


def _volume_is_empty(volume: str) -> bool:
    result = _docker(
        "run",
        "--rm",
        "--mount",
        f"type=volume,src={volume},dst=/data,readonly",
        _HELPER_IMAGE,
        "sh",
        "-ec",
        "test -z \"$(find /data -mindepth 1 -print -quit)\"",
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(result.stdout + result.stderr)
    return result.returncode == 0


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
) -> dict[str, object]:
    parsed = urlsplit(url)
    headers = {
        "Content-Type": "application/json",
        "Origin": f"{parsed.scheme}://{parsed.netloc}",
    }
    if csrf_token is not None:
        headers["X-CSRF-Token"] = csrf_token
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


def _base_url(env: dict[str, str], project: str) -> str:
    port = _compose(env, project, "port", "muzilla", "8080").strip().rsplit(":", 1)[1]
    return f"http://127.0.0.1:{port}/api"


def _assert_exact_image(env: dict[str, str], project: str) -> None:
    assert IMAGE is not None
    container_id = _compose(env, project, "ps", "-q", "muzilla").strip()
    inspected = json.loads(_docker("inspect", container_id).stdout)[0]
    assert inspected["Config"]["Image"] == IMAGE


def _assert_persisted_state(base_url: str) -> None:
    assert _json_url(f"{base_url}/ready")["status"] == "ready"
    settings = _json_url(f"{base_url}/settings")
    discogs = next(
        item for item in settings["providers"]  # type: ignore[union-attr]
        if item["provider"] == "discogs"
    )
    assert discogs["enabled"] is False
    assert discogs["token_configured"] is True
    assert len(_json_url(f"{base_url}/tracks?limit=10")["items"]) == 1  # type: ignore[arg-type]


def _write_archive(source_volume: str, backup_dir: Path) -> tuple[Path, Path]:
    archive = backup_dir / "muzilla-data.tar"
    manifest = backup_dir / "muzilla-data.tar.sha256"
    _docker(
        "run",
        "--rm",
        "--mount",
        f"type=volume,src={source_volume},dst=/data,readonly",
        "--mount",
        f"type=bind,src={backup_dir},dst=/backup",
        _HELPER_IMAGE,
        "sh",
        "-ec",
        "tar -C /data -cf /backup/muzilla-data.tar . && "
        "sha256sum /backup/muzilla-data.tar > /backup/muzilla-data.tar.sha256 && "
        "chmod 644 /backup/muzilla-data.tar /backup/muzilla-data.tar.sha256",
    )
    manifest_parts = manifest.read_text(encoding="utf-8").split()
    assert archive.is_file()
    assert manifest_parts == [_sha256(archive), "/backup/muzilla-data.tar"]
    return archive, manifest


def _restore_archive(archive: Path, manifest: Path, destination_volume: str) -> None:
    manifest_parts = manifest.read_text(encoding="utf-8").split()
    if manifest_parts != [_sha256(archive), "/backup/muzilla-data.tar"]:
        raise RuntimeError("backup archive checksum mismatch")
    if not _volume_is_empty(destination_volume):
        raise RuntimeError("restore destination volume is not empty")
    _docker(
        "run",
        "--rm",
        "--mount",
        f"type=volume,src={destination_volume},dst=/data",
        "--mount",
        f"type=bind,src={archive.parent},dst=/backup,readonly",
        _HELPER_IMAGE,
        "sh",
        "-ec",
        "tar -C /data -xf /backup/muzilla-data.tar",
    )


def _seed_nonempty_volume(volume: str) -> None:
    _docker(
        "run",
        "--rm",
        "--mount",
        f"type=volume,src={volume},dst=/data",
        _HELPER_IMAGE,
        "sh",
        "-ec",
        "printf sentinel > /data/restore-must-not-overwrite",
    )


def _assert_rejected_restore(
    archive: Path,
    manifest: Path,
    destination_volume: str,
    source_volume: str,
    music_fixture: Path,
    *,
    message: str,
) -> None:
    destination_before = _volume_fingerprint(destination_volume)
    source_before = _volume_fingerprint(source_volume)
    music_before = _sha256(music_fixture)
    try:
        _restore_archive(archive, manifest, destination_volume)
    except RuntimeError as exc:
        assert message in str(exc)
    else:
        raise AssertionError("unsafe restore was accepted")
    assert _volume_fingerprint(destination_volume) == destination_before
    assert _volume_fingerprint(source_volume) == source_before
    assert _sha256(music_fixture) == music_before


def _stop_cleanly(env: dict[str, str], project: str) -> None:
    _compose(env, project, "stop", "muzilla")
    container_id = _compose(env, project, "ps", "-aq", "muzilla").strip()
    inspected = json.loads(_docker("inspect", container_id).stdout)[0]
    assert inspected["State"]["Status"] == "exited"
    assert inspected["State"]["ExitCode"] == 0


def _write_compose_override(path: Path, library: Path) -> None:
    path.write_text(
        "services:\n"
        "  muzilla:\n"
        "    volumes:\n"
        "      - type: volume\n"
        "        source: isolated-data\n"
        "        target: /data\n"
        "      - type: bind\n"
        f"        source: {json.dumps(str(library))}\n"
        "        target: /music\n"
        "        read_only: true\n"
        "volumes:\n"
        "  isolated-data:\n"
        "    external: true\n"
        "    name: ${MUZILLA_TEST_DATA_VOLUME}\n",
        encoding="utf-8",
    )


def main() -> None:
    if IMAGE is None:
        raise SystemExit("set MUZILLA_TEST_IMAGE to a built image tag")

    suffix = str(os.getpid())
    project = f"muzilla-backup-restore-{suffix}"
    source_volume = f"muzilla-backup-source-{suffix}"
    destination_volume = f"muzilla-backup-destination-{suffix}"
    _docker("volume", "create", source_volume)
    _docker("volume", "create", destination_volume)
    with tempfile.TemporaryDirectory(prefix="muzilla-backup-restore-") as temp_dir:
        temp = Path(temp_dir)
        library = temp / "music"
        library.mkdir()
        music_fixture = library / "backup-restore-fixture.mp3"
        shutil.copyfile(REPO_ROOT / "tests/fixtures/audio/silence.mp3", music_fixture)
        music_hash = _sha256(music_fixture)
        backup_dir = temp / "backup"
        backup_dir.mkdir()
        override = temp / "compose.backup-restore-smoke.yaml"
        _write_compose_override(override, library)
        env = {
            **os.environ,
            "MUZILLA_AUTH__ENABLED": "false",
            "MUZILLA_BIND_ADDRESS": "127.0.0.1",
            "MUZILLA_IMAGE": IMAGE,
            "MUZILLA_LIBRARY_PATH": str(library),
            "MUZILLA_PORT": "0",
            "MUZILLA_SMOKE_OVERRIDE": str(override),
            "MUZILLA_TEST_DATA_VOLUME": source_volume,
        }
        try:
            _compose(env, project, "up", "-d", "--no-build", "--wait", "--wait-timeout", "60")
            source_url = _base_url(env, project)
            _assert_exact_image(env, project)
            auth_status = _json_url(f"{source_url}/auth/status")
            provider = _json_request(
                f"{source_url}/settings/providers/discogs",
                method="PUT",
                body={"enabled": False, "token": "isolated-backup-secret"},
                csrf_token=str(auth_status["csrf_token"]),
            )
            assert provider["token_configured"] is True
            scan = _json_request(source_url + "/scan", method="POST", body={"root": "/music"})
            _wait_for_job(source_url, int(scan["job_id"]))
            _assert_persisted_state(source_url)
            assert _sha256(music_fixture) == music_hash

            # Stop before archiving: source SQLite, settings, and the managed
            # secret store form one stable /data checkpoint.
            _stop_cleanly(env, project)
            archive, manifest = _write_archive(source_volume, backup_dir)
            source_fingerprint = _volume_fingerprint(source_volume)

            corrupt_archive = backup_dir / "muzilla-data-corrupt.tar"
            shutil.copyfile(archive, corrupt_archive)
            with corrupt_archive.open("ab") as corrupt:
                corrupt.write(b"corrupt")
            _assert_rejected_restore(
                corrupt_archive,
                manifest,
                destination_volume,
                source_volume,
                music_fixture,
                message="checksum mismatch",
            )
            assert _volume_is_empty(destination_volume)

            _seed_nonempty_volume(destination_volume)
            _assert_rejected_restore(
                archive,
                manifest,
                destination_volume,
                source_volume,
                music_fixture,
                message="not empty",
            )

            _docker("volume", "rm", destination_volume)
            _docker("volume", "create", destination_volume)
            _restore_archive(archive, manifest, destination_volume)
            assert _volume_fingerprint(destination_volume) == source_fingerprint
            assert _sha256(music_fixture) == music_hash

            restored_env = {**env, "MUZILLA_TEST_DATA_VOLUME": destination_volume}
            _compose(
                restored_env,
                project,
                "up",
                "-d",
                "--no-build",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "60",
            )
            _assert_exact_image(restored_env, project)
            _assert_persisted_state(_base_url(restored_env, project))
            assert _sha256(music_fixture) == music_hash
        except Exception:
            logs = _compose(env, project, "logs", "--no-color", check=False)
            if logs:
                print(logs)
            raise
        finally:
            _compose(env, project, "down", "--remove-orphans", check=False)
            _docker("volume", "rm", source_volume, check=False)
            _docker("volume", "rm", destination_volume, check=False)


if __name__ == "__main__":
    main()
