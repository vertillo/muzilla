from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from muzilla.tags.reader import read_track


def _mpeg_payload_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as audio:
        header = audio.read(10)
        start = 0
        if header[:3] == b"ID3" and len(header) == 10:
            tag_size = sum(
                (header[index] & 0x7F) << shift
                for index, shift in zip((6, 7, 8, 9), (21, 14, 7, 0), strict=True)
            )
            start = 10 + tag_size + (10 if header[5] & 0x10 else 0)
        end = path.stat().st_size
        if end >= 128:
            audio.seek(end - 128)
            if audio.read(3) == b"TAG":
                end -= 128
        audio.seek(start)
        while chunk := audio.read(1024 * 1024):
            if audio.tell() > end:
                chunk = chunk[: end - (audio.tell() - len(chunk))]
            digest.update(chunk)
            if audio.tell() >= end:
                break
    return digest.hexdigest()


def _restore_in_isolated_process(path: Path) -> dict[str, Any]:
    script = """
import json, resource, sys
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from muzilla.changes.writer import restore_from_before_blob

def peak_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)

path = Path(sys.argv[1])
engine = create_engine('sqlite://')
with Session(engine) as session:
    before_peak = peak_bytes()
    before = path.stat()
    restore_from_before_blob(
        session,
        path,
        {'title': 'RSS bounded restore'},
        blob_store=None,
        library_root=path.parent,
    )
    after_peak = peak_bytes()
    after = path.stat()
    print(json.dumps({
        'size_bytes': after.st_size,
        'peak_growth_bytes': max(0, after_peak - before_peak),
        'mode_before': before.st_mode & 0o777,
        'mode_after': after.st_mode & 0o777,
        'mtime_before_ns': before.st_mtime_ns,
        'mtime_after_ns': after.st_mtime_ns,
        'atime_before_ns': before.st_atime_ns,
        'atime_after_ns': after.st_atime_ns,
    }))
engine.dispose()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return payload


def test_restore_streams_realistic_mp3_with_bounded_rss_and_preserved_audio(
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required to generate realistic increasing MP3 fixtures")

    measurements: list[dict[str, Any]] = []
    payload_hashes: list[str] = []
    for duration_seconds in (30, 1200):
        audio = tmp_path / f"restore-{duration_seconds}.mp3"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100",
                "-t",
                str(duration_seconds),
                "-ac",
                "2",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "320k",
                "-write_xing",
                "0",
                "-id3v2_version",
                "3",
                "-y",
                str(audio),
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
        os.chmod(audio, 0o640)
        preserved_time_ns = 1_234_567_890_123_456_000
        os.utime(audio, ns=(preserved_time_ns, preserved_time_ns))
        payload_hashes.append(_mpeg_payload_hash(audio))
        measurement = _restore_in_isolated_process(audio)
        measurements.append(measurement)
        assert measurement["mode_before"] == measurement["mode_after"] == 0o640
        assert measurement["mtime_before_ns"] == measurement["mtime_after_ns"]
        assert measurement["atime_before_ns"] == measurement["atime_after_ns"]
        assert read_track(audio).title == "RSS bounded restore"

    small, large = measurements
    assert large["size_bytes"] > small["size_bytes"] * 20
    assert large["peak_growth_bytes"] <= 24 * 1024 * 1024
    assert large["peak_growth_bytes"] <= small["peak_growth_bytes"] + 16 * 1024 * 1024
    assert [
        _mpeg_payload_hash(tmp_path / f"restore-{duration}.mp3") for duration in (30, 1200)
    ] == payload_hashes
