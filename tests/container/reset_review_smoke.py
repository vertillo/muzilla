"""Exact-image reset recovery smoke on disposable Compose named volumes.

This harness seeds representative ReviewBundle state through backend fixtures,
uses the authenticated API for resets, and never applies metadata to /music.
All provider integrations are disabled. Without ``--keep-running``, every
container, volume and temporary Compose file created here is removed.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_SCRIPT = REPO_ROOT / "tests/container/reset_review_fixture.py"
AUDIO_FIXTURE = REPO_ROOT / "tests/fixtures/audio/silence.mp3"
IMAGE = os.environ.get("MUZILLA_TEST_IMAGE")
EXPECTED_IMAGE_ID = os.environ.get("MUZILLA_TEST_IMAGE_ID")
SMOKE_PASSWORD = "candidate-reset-smoke-only-password"
SMOKE_SECRET = "candidate-reset-managed-secret-not-real"
CATALOG_KEY = "candidate-catalog-reset-idempotency"
FACTORY_KEY = "candidate-factory-reset-fault"


class HttpClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    @property
    def origin(self) -> str:
        parsed = urlsplit(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object], dict[str, str]]:
        request_headers = dict(headers or {})
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            request_headers.setdefault("Content-Type", "application/json")
            request_headers.setdefault("Origin", self.origin)
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=payload,
            headers=request_headers,
            method=method,
        )
        try:
            response = self.opener.open(request, timeout=35)
        except urllib.error.HTTPError as error:
            response = error
        raw_body = response.read()
        decoded: dict[str, object]
        if raw_body:
            value = json.loads(raw_body)
            decoded = cast(dict[str, object], value) if isinstance(value, dict) else {"data": value}
        else:
            decoded = {}
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        return cast(int, response.status), decoded, cast(dict[str, str], response_headers)

    def csrf_token(self) -> str:
        status, body, _ = self.request("/auth/status")
        assert status == 200, body
        token = body.get("csrf_token")
        assert isinstance(token, str) and token
        return token

    def login(self) -> None:
        status, body, _ = self.request(
            "/auth/login", method="POST", body={"password": SMOKE_PASSWORD}
        )
        assert status == 200, body
        assert body.get("authenticated") is True

    def cookie(self) -> str:
        values = [cookie.value for cookie in self.cookies if cookie.name == "muzilla_session"]
        assert len(values) == 1
        return cast(str, values[0])


def _docker(*args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed:\n{result.stdout}{result.stderr}")
    return result


def _compose(
    env: dict[str, str],
    project: str,
    *args: str,
    check: bool = True,
    timeout: int = 180,
) -> str:
    command = [
        "docker",
        "compose",
        "--env-file",
        os.devnull,
        "-p",
        project,
        "-f",
        str(REPO_ROOT / "docker-compose.yml"),
        "-f",
        env["MUZILLA_RESET_SMOKE_OVERRIDE"],
        *args,
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed:\n{result.stdout}{result.stderr}")
    return result.stdout


def _write_compose_override(path: Path, fixture_path: Path) -> None:
    path.write_text(
        "services:\n"
        "  muzilla:\n"
        "    environment:\n"
        "      MUZILLA_STORAGE__BACKUP_DIR: /backups\n"
        '      MUZILLA_PROVIDERS__MUSICBRAINZ__ENABLED: "false"\n'
        '      MUZILLA_PROVIDERS__DISCOGS__ENABLED: "false"\n'
        '      MUZILLA_PROVIDERS__DEEZER__ENABLED: "false"\n'
        '      MUZILLA_PROVIDERS__ACOUSTID__ENABLED: "false"\n'
        '      MUZILLA_PROVIDERS__COVERARTARCHIVE__ENABLED: "false"\n'
        '      MUZILLA_PROVIDERS__LRCLIB__ENABLED: "false"\n'
        "    volumes:\n"
        "      - type: volume\n"
        "        source: isolated-data\n"
        "        target: /data\n"
        "      - type: volume\n"
        "        source: isolated-music\n"
        "        target: /music\n"
        "      - type: volume\n"
        "        source: isolated-backups\n"
        "        target: /backups\n"
        "        read_only: true\n"
        "      - type: bind\n"
        f"        source: {json.dumps(str(fixture_path))}\n"
        "        target: /tmp/reset_review_fixture.py\n"
        "        read_only: true\n"
        "volumes:\n"
        "  isolated-data:\n"
        "    external: true\n"
        "    name: ${MUZILLA_TEST_DATA_VOLUME}\n"
        "  isolated-music:\n"
        "    external: true\n"
        "    name: ${MUZILLA_TEST_MUSIC_VOLUME}\n"
        "  isolated-backups:\n"
        "    external: true\n"
        "    name: ${MUZILLA_TEST_BACKUP_VOLUME}\n",
        encoding="utf-8",
    )


def _compose_env(override: Path, *, data: str, music: str, backups: str) -> dict[str, str]:
    # Do not pass host Muzilla configuration/secrets to Compose; this smoke has
    # isolated credentials, disabled providers and dedicated named volumes.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MUZILLA_", "COMPOSE_"))
    }
    env.update(
        {
            "MUZILLA_AUTH__ENABLED": "true",
            "MUZILLA_AUTH__PASSWORD": SMOKE_PASSWORD,
            "MUZILLA_AUTH__SESSION_SECRET": "candidate-reset-session-secret-not-for-production",
            "MUZILLA_BIND_ADDRESS": "127.0.0.1",
            "MUZILLA_IMAGE": cast(str, IMAGE),
            "MUZILLA_LIBRARY_PATH": str(REPO_ROOT / "music"),
            "MUZILLA_PORT": "0",
            "MUZILLA_RESET_SMOKE_OVERRIDE": str(override),
            "MUZILLA_TEST_DATA_VOLUME": data,
            "MUZILLA_TEST_MUSIC_VOLUME": music,
            "MUZILLA_TEST_BACKUP_VOLUME": backups,
        }
    )
    return env


def _run_fixture(env: dict[str, str], project: str, *args: str) -> dict[str, object]:
    output = _compose(
        env, project, "exec", "-T", "muzilla", "python", "/tmp/reset_review_fixture.py", *args
    )
    lines = [line for line in output.splitlines() if line.strip()]
    assert lines, output
    decoded = json.loads(lines[-1])
    assert isinstance(decoded, dict)
    return cast(dict[str, object], decoded)


def _run_image_python(*args: str, mounts: tuple[str, ...], user: str | None = None) -> str:
    result = _docker(
        "run",
        "--rm",
        "--network",
        "none",
        *(["--user", user] if user is not None else []),
        *[argument for mount in mounts for argument in ("--mount", mount)],
        "--mount",
        f"type=bind,src={FIXTURE_SCRIPT},dst=/tmp/reset_review_fixture.py,readonly",
        "--entrypoint",
        "python",
        cast(str, IMAGE),
        *args,
    )
    return result.stdout.strip()


def _seed_volume(volume: str, target: str, *, source_fixture: Path | None = None) -> None:
    mounts = [f"type=volume,src={volume},dst={target}"]
    code = (
        "from pathlib import Path; import os, sys; "
        "target=Path(sys.argv[1]); target.mkdir(parents=True, exist_ok=True); "
        "fixture=target/'reset-review-fixture.mp3'; "
        "fixture.write_bytes(Path('/fixture.mp3').read_bytes()); "
        "os.chown(target, 1000, 1000); os.chown(fixture, 1000, 1000)"
        if target == "/music"
        else "from pathlib import Path; import sys; "
        "target=Path(sys.argv[1]); target.mkdir(parents=True, exist_ok=True); "
        "(target/'backup-sentinel.bin').write_bytes(b'candidate reset backup sentinel\\n')"
    )
    if source_fixture is not None:
        mounts.append(f"type=bind,src={source_fixture},dst=/fixture.mp3,readonly")
    _run_image_python("-c", code, target, mounts=tuple(mounts), user="0:0")


def _prepare_volumes(
    volumes: dict[str, str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    created: list[str] = []
    try:
        for volume in volumes.values():
            _docker("volume", "create", volume)
            created.append(volume)
        _seed_volume(volumes["music"], "/music", source_fixture=AUDIO_FIXTURE)
        _seed_volume(volumes["backups"], "/backups")
        music_before = _volume_snapshot(volumes["music"], "/music")
        backup_before = _volume_snapshot(volumes["backups"], "/backups")
        assert [item["path"] for item in music_before] == ["reset-review-fixture.mp3"]
        assert [item["path"] for item in backup_before] == ["backup-sentinel.bin"]
        return music_before, backup_before
    except Exception:
        for volume in created:
            _docker("volume", "rm", volume, check=False)
        raise


def _volume_snapshot(volume: str, target: str) -> list[dict[str, object]]:
    code = """import hashlib, json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
items = []
for path in sorted(root.rglob('*')):
    if path.is_symlink():
        raise AssertionError(f'fixture volume contains symlink: {path}')
    if path.is_file():
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        items.append({'path': path.relative_to(root).as_posix(), 'sha256': digest,
                      'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                      'mode': stat.st_mode & 0o777, 'inode': stat.st_ino})
print(json.dumps(items, sort_keys=True))
"""
    output = _run_image_python(
        "-c",
        code,
        target,
        mounts=(f"type=volume,src={volume},dst={target},readonly",),
    )
    decoded = json.loads(output)
    assert isinstance(decoded, list) and decoded
    return cast(list[dict[str, object]], decoded)


def _assert_exact_image(env: dict[str, str], project: str) -> dict[str, str]:
    container_id = _compose(env, project, "ps", "-q", "muzilla").strip()
    assert container_id
    inspected = json.loads(_docker("inspect", container_id).stdout)[0]
    image_id = str(inspected["Image"])
    assert inspected["Config"]["Image"] == IMAGE
    mounts = {mount["Destination"]: mount for mount in inspected["Mounts"]}
    for destination, env_key in (
        ("/data", "MUZILLA_TEST_DATA_VOLUME"),
        ("/music", "MUZILLA_TEST_MUSIC_VOLUME"),
        ("/backups", "MUZILLA_TEST_BACKUP_VOLUME"),
    ):
        mount = mounts[destination]
        assert mount["Type"] == "volume"
        assert mount["Name"] == env[env_key]
    assert mounts["/backups"]["RW"] is False
    fixture_mount = mounts["/tmp/reset_review_fixture.py"]
    assert fixture_mount["Type"] == "bind" and fixture_mount["RW"] is False
    image_info = json.loads(_docker("image", "inspect", cast(str, IMAGE)).stdout)[0]
    assert image_info["Id"] == image_id
    if EXPECTED_IMAGE_ID is not None:
        assert image_id == EXPECTED_IMAGE_ID
    return {"image": cast(str, IMAGE), "image_id": image_id, "container_id": container_id}


def _base_url(env: dict[str, str], project: str) -> str:
    published = _compose(env, project, "port", "muzilla", "8080").strip().rsplit(":", 1)
    assert len(published) == 2 and published[1].isdigit(), published
    return f"http://127.0.0.1:{published[1]}/api"


def _wait_ready(client: HttpClient, *, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    last_error: object = "not attempted"
    while time.monotonic() < deadline:
        try:
            status, body, _ = client.request("/ready")
            if status == 200 and body.get("status") == "ready":
                return
            last_error = (status, body)
        except (OSError, urllib.error.URLError) as exc:
            last_error = repr(exc)
        time.sleep(0.25)
    raise RuntimeError(f"candidate app did not become ready: {last_error}")


def _assert_provider(client: HttpClient, *, configured: bool) -> dict[str, object]:
    status, settings, _ = client.request("/settings")
    assert status == 200, settings
    providers = settings.get("providers")
    assert isinstance(providers, list)
    discogs = next(item for item in providers if item["provider"] == "discogs")
    assert discogs["enabled"] is False
    assert discogs["token_configured"] is configured
    assert SMOKE_SECRET not in json.dumps(settings)
    return cast(dict[str, object], discogs)


def _put_managed_secret(client: HttpClient, *, idempotency_key: str) -> None:
    status, body, _ = client.request(
        "/settings/providers/discogs",
        method="PUT",
        body={"enabled": False, "token": SMOKE_SECRET},
        headers={"X-CSRF-Token": client.csrf_token(), "Idempotency-Key": idempotency_key},
    )
    assert status == 200, body
    assert body.get("enabled") is False and body.get("token_configured") is True
    assert SMOKE_SECRET not in json.dumps(body)


def _post_catalog_reset(
    client: HttpClient, *, key: str, csrf: str | None
) -> tuple[int, dict[str, object], dict[str, str]]:
    headers = {"Idempotency-Key": key}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return client.request(
        "/settings/reset/catalog",
        method="POST",
        body={"scope": "catalog_and_activity", "confirmation": "RESET CATALOG AND ACTIVITY"},
        headers=headers,
    )


def _post_factory_reset(
    client: HttpClient, *, key: str, csrf: str, password: str = SMOKE_PASSWORD
) -> tuple[int, dict[str, object], dict[str, str]]:
    return client.request(
        "/settings/reset/factory",
        method="POST",
        body={"scope": "factory", "confirmation": "FACTORY RESET MUZILLA", "password": password},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": key},
    )


def _assert_reset_result(
    result: dict[str, object], *, scope: str, operation_id: int | None = None
) -> int:
    assert result.get("scope") == scope
    assert result.get("state") == "succeeded"
    assert result.get("music_files_touched") is False
    expected_preserved = scope == "catalog_and_activity"
    assert result.get("settings_preserved") is expected_preserved
    assert result.get("secrets_preserved") is expected_preserved
    returned_id = result.get("operation_id")
    assert isinstance(returned_id, int)
    if operation_id is not None:
        assert returned_id == operation_id
    return returned_id


def _create_fault(env: dict[str, str], project: str, *, enabled: bool) -> None:
    action = (
        "CREATE TRIGGER candidate_reset_inject_track_delete BEFORE DELETE ON tracks "
        "BEGIN SELECT RAISE(ABORT, 'injected disposable reset deletion fault'); END"
        if enabled
        else "DROP TRIGGER IF EXISTS candidate_reset_inject_track_delete"
    )
    code = (
        "import sqlite3; connection=sqlite3.connect('/data/muzilla.db', timeout=30); "
        "connection.execute('PRAGMA busy_timeout=30000'); "
        f"connection.execute({action!r}); connection.commit(); connection.close()"
    )
    _compose(env, project, "exec", "-T", "muzilla", "python", "-c", code)


def _negative_auth_and_csrf_checks(base_url: str, client: HttpClient) -> None:
    anonymous = HttpClient(base_url)
    status, _, _ = _post_catalog_reset(anonymous, key="negative-unauthenticated", csrf="invalid")
    assert status == 401

    missing_status, _, _ = _post_catalog_reset(client, key="negative-missing-csrf", csrf=None)
    assert missing_status == 403

    csrf = client.csrf_token()
    cross_origin_status, _, _ = client.request(
        "/settings/reset/catalog",
        method="POST",
        body={"scope": "catalog_and_activity", "confirmation": "RESET CATALOG AND ACTIVITY"},
        headers={
            "Idempotency-Key": "negative-cross-origin",
            "X-CSRF-Token": csrf,
            "Origin": "https://reset-smoke.invalid",
        },
    )
    assert cross_origin_status == 403


def _check_music_and_backup(
    music_volume: str,
    backup_volume: str,
    music_before: list[dict[str, object]],
    backup_before: list[dict[str, object]],
    *,
    phase: str,
) -> None:
    music_after = _volume_snapshot(music_volume, "/music")
    backup_after = _volume_snapshot(backup_volume, "/backups")
    assert music_after == music_before, f"music volume changed during {phase}"
    assert backup_after == backup_before, f"backup volume changed during {phase}"


def _case_sensitive_image_run(volume: str) -> dict[str, object]:
    _docker("volume", "create", volume)
    mounts = (f"type=volume,src={volume},dst=/music",)
    try:
        _run_image_python(
            "-c",
            "from pathlib import Path; import os; root=Path('/music'); "
            "sentinel=root/'.candidate-reset-volume-init'; sentinel.touch(); "
            "os.chown(root, 1000, 1000); os.chown(sentinel, 1000, 1000)",
            mounts=mounts,
            user="0:0",
        )
        output = _run_image_python(
            "/tmp/reset_review_fixture.py",
            "case",
            mounts=mounts,
        )
        decoded = json.loads(output)
        assert isinstance(decoded, dict)
        assert decoded.get("filesystem_case_sensitive") is True
        assert decoded.get("exact_source_name") == "Track.mp3"
        assert decoded.get("exact_destination_name") == "track.mp3"
        return cast(dict[str, object], decoded)
    finally:
        _docker("volume", "rm", volume, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="leave the verified Compose app and its volumes running for browser acceptance",
    )
    args = parser.parse_args()
    if IMAGE is None:
        raise SystemExit("set MUZILLA_TEST_IMAGE to the exact built candidate image tag")
    if not AUDIO_FIXTURE.is_file() or not FIXTURE_SCRIPT.is_file():
        raise SystemExit("the deterministic audio fixture or reset fixture script is missing")

    image_info = json.loads(_docker("image", "inspect", IMAGE).stdout)[0]
    image_id = str(image_info["Id"])
    if EXPECTED_IMAGE_ID is not None and image_id != EXPECTED_IMAGE_ID:
        raise SystemExit(
            f"image id mismatch: MUZILLA_TEST_IMAGE_ID={EXPECTED_IMAGE_ID}, resolved={image_id}"
        )

    suffix = str(os.getpid())
    project = f"muzilla-reset-review-{suffix}"
    volumes = {
        "data": f"muzilla-reset-review-data-{suffix}",
        "music": f"muzilla-reset-review-music-{suffix}",
        "backups": f"muzilla-reset-review-backups-{suffix}",
    }
    case_volume = f"muzilla-reset-review-case-{suffix}"
    temp_root = Path(tempfile.mkdtemp(prefix="muzilla-reset-review-"))
    override = temp_root / "compose.reset-review.yaml"
    try:
        _write_compose_override(override, FIXTURE_SCRIPT)
        music_before, backup_before = _prepare_volumes(volumes)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    env = _compose_env(
        override, data=volumes["data"], music=volumes["music"], backups=volumes["backups"]
    )
    keep = False
    runtime: dict[str, str] = {"image": IMAGE, "image_id": image_id}
    client: HttpClient | None = None

    try:
        _compose(env, project, "up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
        runtime = {**runtime, **_assert_exact_image(env, project)}
        base_url = _base_url(env, project)
        client = HttpClient(base_url)
        _wait_ready(client)
        client.login()
        _negative_auth_and_csrf_checks(base_url, client)

        _put_managed_secret(client, idempotency_key="provider-secret-before-catalog")
        seed_before_catalog = _run_fixture(env, project, "seed")
        seeded = _run_fixture(env, project, "assert-seeded")
        assert seeded["revisions"] == 6 and seeded["journals"] == 2
        assert seeded["auth_epoch"] == seed_before_catalog["auth_epoch"]
        _assert_provider(client, configured=True)

        catalog_status, catalog_result, _ = _post_catalog_reset(
            client, key=CATALOG_KEY, csrf=client.csrf_token()
        )
        assert catalog_status == 200, catalog_result
        catalog_operation_id = _assert_reset_result(catalog_result, scope="catalog_and_activity")
        deleted_counts = catalog_result.get("deleted_counts")
        assert isinstance(deleted_counts, dict)
        assert deleted_counts.get("review_bundles") == 3
        assert deleted_counts.get("proposal_revisions") == 6
        assert _run_fixture(env, project, "assert-clean", "catalog_and_activity")["settings"] == 1
        catalog_audit = _run_fixture(
            env, project, "audit", CATALOG_KEY, "catalog_and_activity", "succeeded", "completed"
        )
        assert catalog_audit["operation_id"] == catalog_operation_id
        _assert_provider(client, configured=True)
        replay_status, catalog_replay, _ = _post_catalog_reset(
            client, key=CATALOG_KEY, csrf=client.csrf_token()
        )
        assert replay_status == 200 and catalog_replay == catalog_result
        _check_music_and_backup(
            volumes["music"], volumes["backups"], music_before, backup_before, phase="catalog reset"
        )

        _compose(
            env,
            project,
            "up",
            "-d",
            "--no-build",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "90",
        )
        runtime = {**runtime, **_assert_exact_image(env, project)}
        client.base_url = _base_url(env, project)
        _wait_ready(client)
        status, auth_status, _ = client.request("/auth/status")
        assert status == 200 and auth_status.get("authenticated") is True
        assert _assert_provider(client, configured=True)["token_configured"] is True
        _run_fixture(env, project, "assert-clean", "catalog_and_activity")
        replay_status, post_restart_replay, _ = _post_catalog_reset(
            client, key=CATALOG_KEY, csrf=client.csrf_token()
        )
        assert replay_status == 200 and post_restart_replay == catalog_result
        _check_music_and_backup(
            volumes["music"],
            volumes["backups"],
            music_before,
            backup_before,
            phase="catalog restart",
        )

        _put_managed_secret(client, idempotency_key="provider-secret-before-factory")
        _run_fixture(env, project, "seed")
        factory_seeded = _run_fixture(env, project, "assert-seeded")
        epoch_before_factory = cast(int, factory_seeded["auth_epoch"])
        old_cookie = client.cookie()
        bad_password_status, _, _ = _post_factory_reset(
            client,
            key="negative-factory-password",
            csrf=client.csrf_token(),
            password="wrong-candidate-reset-password",
        )
        assert bad_password_status == 403
        fault_audit_before = _run_fixture(env, project, "assert-seeded")
        assert fault_audit_before["auth_epoch"] == epoch_before_factory

        _create_fault(env, project, enabled=True)
        fault_status, fault_body, fault_headers = _post_factory_reset(
            client, key=FACTORY_KEY, csrf=client.csrf_token()
        )
        assert fault_status == 503, fault_body
        assert fault_body.get("detail") == "database cleanup incomplete; retry is required"
        assert fault_headers.get("retry-after") == "1"
        interrupted = _run_fixture(env, project, "assert-interrupted")
        assert interrupted["auth_epoch"] == epoch_before_factory
        fault_audit = _run_fixture(
            env, project, "audit", FACTORY_KEY, "factory", "running", "prepared"
        )
        _check_music_and_backup(
            volumes["music"],
            volumes["backups"],
            music_before,
            backup_before,
            phase="failed factory reset",
        )

        _create_fault(env, project, enabled=False)
        _compose(
            env,
            project,
            "up",
            "-d",
            "--no-build",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "90",
        )
        runtime = {**runtime, **_assert_exact_image(env, project)}
        client.base_url = _base_url(env, project)
        _wait_ready(client)
        status, auth_status, _ = client.request("/auth/status")
        assert status == 200 and auth_status.get("authenticated") is False
        old_session = HttpClient(client.base_url)
        old_status, _, _ = old_session.request(
            "/tracks?limit=10", headers={"Cookie": f"muzilla_session={old_cookie}"}
        )
        assert old_status == 401
        factory_audit = _run_fixture(
            env, project, "audit", FACTORY_KEY, "factory", "succeeded", "completed"
        )
        assert factory_audit["operation_id"] == fault_audit["operation_id"]
        factory_clean = _run_fixture(env, project, "assert-clean", "factory")
        epoch_after_factory = cast(int, factory_clean["auth_epoch"])
        assert epoch_after_factory == epoch_before_factory + 1
        catalog_audit = _run_fixture(
            env, project, "audit", CATALOG_KEY, "catalog_and_activity", "succeeded", "completed"
        )
        assert catalog_audit["operation_id"] == catalog_operation_id
        client.login()
        _assert_provider(client, configured=False)
        replay_status, factory_replay, _ = _post_factory_reset(
            client, key=FACTORY_KEY, csrf=client.csrf_token()
        )
        assert replay_status == 200
        replay_operation_id = _assert_reset_result(factory_replay, scope="factory")
        assert replay_operation_id == factory_audit["operation_id"]
        assert (
            _run_fixture(env, project, "assert-clean", "factory")["auth_epoch"]
            == epoch_after_factory
        )
        _check_music_and_backup(
            volumes["music"],
            volumes["backups"],
            music_before,
            backup_before,
            phase="factory recovery",
        )

        case_result = _case_sensitive_image_run(case_volume)
        if args.keep_running:
            # Restore representative catalog state only as a backend fixture so
            # independent browser acceptance can inspect/reset it through the UI.
            client.login()
            _put_managed_secret(client, idempotency_key="provider-secret-for-browser")
            _run_fixture(env, project, "seed")
            _run_fixture(env, project, "assert-seeded")
            keep = True
            runtime["url"] = client.base_url.replace("/api", "")
            runtime["project"] = project
            runtime["data_volume"] = volumes["data"]
            runtime["music_volume"] = volumes["music"]
            runtime["backup_volume"] = volumes["backups"]
            runtime["compose_override"] = str(override)
            runtime["compose_temp_root"] = str(temp_root)

        report = {
            "candidate_image": runtime,
            "reset_cases": [
                "catalog reset removed persisted ready/applied/undone reviews, six revisions, two apply runs, one undo run, journals and jobs",
                "catalog reset preserved DB settings, managed provider secret, auth session, music and backup hashes across restart",
                "factory reset failed transactionally at an injected track-delete trigger and converged during app startup in a new process",
                "factory recovery removed DB settings and managed provider secret, revoked the captured session exactly once, and retained successful idempotency/audit records",
            ],
            "music_volume": music_before,
            "backup_volume": backup_before,
            "case_sensitive_image_volume": case_result,
            "browser_runtime_kept": keep,
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        if keep:
            print(
                "Browser acceptance: use the URL above and password "
                f"{SMOKE_PASSWORD!r}; after testing, remove only this isolated Compose project/volumes."
            )
    except Exception:
        logs = _compose(env, project, "logs", "--no-color", check=False, timeout=60)
        if logs:
            print(logs)
        raise
    finally:
        if keep:
            cleanup_env = shlex.join(
                [
                    "env",
                    f"MUZILLA_IMAGE={env['MUZILLA_IMAGE']}",
                    f"MUZILLA_PORT={env['MUZILLA_PORT']}",
                    f"MUZILLA_LIBRARY_PATH={env['MUZILLA_LIBRARY_PATH']}",
                    f"MUZILLA_TEST_DATA_VOLUME={volumes['data']}",
                    f"MUZILLA_TEST_MUSIC_VOLUME={volumes['music']}",
                    f"MUZILLA_TEST_BACKUP_VOLUME={volumes['backups']}",
                    "docker",
                    "compose",
                    "--env-file",
                    "/dev/null",
                    "-p",
                    project,
                    "-f",
                    str(REPO_ROOT / "docker-compose.yml"),
                    "-f",
                    str(override),
                    "down",
                    "--remove-orphans",
                ]
            )
            cleanup_volumes = shlex.join(["docker", "volume", "rm", *volumes.values()])
            print(
                "Cleanup (after browser acceptance):\n"
                f"  {cleanup_env}\n"
                f"  {cleanup_volumes}\n"
                f"  rm -rf {shlex.quote(str(temp_root))}"
            )
        else:
            _compose(env, project, "down", "--remove-orphans", check=False)
            for volume in volumes.values():
                _docker("volume", "rm", volume, check=False)
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
