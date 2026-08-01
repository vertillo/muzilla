from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

IMAGE = os.environ.get("MUZILLA_TEST_IMAGE")
FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _docker_run(
    *command: str, options: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
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
            "test \"$(id -u)\" = 1000 && rsgain custom -O tab /fixture.mp3",
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
