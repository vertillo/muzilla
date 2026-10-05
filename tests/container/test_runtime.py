from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

IMAGE = os.environ.get("MUZILLA_TEST_IMAGE")
FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _docker_run(*command: str, options: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    assert IMAGE is not None
    return subprocess.run(
        ["docker", "run", "--rm", *options, IMAGE, *command],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


@unittest.skipUnless(IMAGE, "set MUZILLA_TEST_IMAGE to a built image tag")
class ContainerRuntimeTest(unittest.TestCase):
    def test_python_runtime_matches_its_locked_inventory(self) -> None:
        script = r"""
import importlib.metadata as metadata
import re
from pathlib import Path


def canonicalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        requirement = line.split(";", 1)[0].strip()
        if not requirement:
            continue
        name, separator, version = requirement.partition("==")
        if not separator:
            raise AssertionError(f"inventory entry is not pinned: {line!r}")
        result[canonicalize(name)] = version
    return result


inventory_path = "/usr/share/muzilla/python-runtime-inventory.txt"
lock_path = "/usr/share/muzilla/python-runtime-lock.txt"
recorded = pins(inventory_path)
installed = {
    canonicalize(distribution.metadata["Name"]): distribution.version
    for distribution in metadata.distributions()
    if canonicalize(distribution.metadata["Name"]) != "muzilla"
}
locked_versions = {}
for line in Path(lock_path).read_text().splitlines():
    requirement = line.split(";", 1)[0].strip()
    if requirement:
        name, separator, version = requirement.partition("==")
        if not separator:
            raise AssertionError(f"lock entry is not pinned: {line!r}")
        locked_versions.setdefault(canonicalize(name), set()).add(version)

assert installed == recorded, (installed, recorded)
assert all(version in locked_versions.get(name, set()) for name, version in installed.items())
assert installed.get("urllib3") == "2.8.0", installed.get("urllib3")
assert "mypy" not in installed and "ruff" not in installed
assert metadata.version("muzilla")
print(Path(inventory_path).read_text(), end="")
"""
        result = _docker_run("-c", script, options=("--entrypoint", "python"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("urllib3==2.8.0", result.stdout)
        self.assertNotIn("mypy==", result.stdout)
        self.assertNotIn("ruff==", result.stdout)

    def test_rsgain_binary_and_shared_libraries_are_usable(self) -> None:
        result = _docker_run(
            "-c",
            "ldd /usr/local/bin/rsgain && rsgain --version",
            options=("--entrypoint", "sh"),
        )

        self.assertNotIn("not found", result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rsgain_analyzes_packaged_audio_fixture_as_non_root(self) -> None:
        fixture = (FIXTURES / "silence.mp3").resolve()
        result = _docker_run(
            "-c",
            'test "$(id -u)" = 1000 && rsgain custom -O tab /fixture.mp3',
            options=(
                "--mount",
                f"type=bind,src={fixture},dst=/fixture.mp3,readonly",
                "--entrypoint",
                "sh",
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fixture.mp3", result.stdout)


if __name__ == "__main__":
    unittest.main()
