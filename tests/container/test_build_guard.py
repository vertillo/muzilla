from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

IMAGE = os.environ.get("MUZILLA_TEST_IMAGE")
REPO_ROOT = Path(__file__).parent.parent.parent


@unittest.skipUnless(IMAGE, "set MUZILLA_TEST_IMAGE to a built image tag")
class ContainerBuildGuardTest(unittest.TestCase):
    def test_build_fails_when_a_runtime_library_is_missing(self) -> None:
        result = subprocess.run(
            [
                "docker",
                "build",
                "--build-arg",
                "RSGAIN_TAGLIB_RUNTIME_PACKAGE=",
                "--file",
                "docker/Dockerfile",
                "--progress",
                "plain",
                ".",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        output = result.stdout + result.stderr

        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("libtag.so.2 => not found", output)


if __name__ == "__main__":
    unittest.main()
