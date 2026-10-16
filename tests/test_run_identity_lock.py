# SPDX-License-Identifier: Apache-2.0
"""Replay simultaneous CLI identity claims without starting paid executor work."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.cli.run_sweep import _build_parser, _check_identity
from harness2.demo import DemoAdapter


def claim(root, variant, synchronize):
    adapter = DemoAdapter(root / "demo")
    args = _build_parser().parse_args(["--bench", "jb", "--substrate", "oc", "--k", "1"])
    original = Path.write_text

    def write(path, *args, **kwargs):
        if synchronize and path.name in ("config.json", "config.json.tmp"):
            original(root / (variant + ".ready"), "ready")
            deadline = time.monotonic() + 1
            while len(list(root.glob("*.ready"))) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
        return original(path, *args, **kwargs)

    try:
        with patch.object(Path, "write_text", write):
            _check_identity(adapter, args, root / "namespace")
    except ValueError as exc:
        print(str(exc))
        return 3
    return 0


class RunIdentityLockTests(unittest.TestCase):
    def command(self, root, variant, synchronize=False):
        env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if k in os.environ}
        env.update(
            PYTHONDONTWRITEBYTECODE="1",
            HARNESS2_IMPROVER_MODEL="provider/" + variant,
            PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        )
        return [
            sys.executable,
            str(Path(__file__).resolve()),
            str(root),
            variant,
            "1" if synchronize else "0",
        ], env

    def test_two_different_simultaneous_claims_cannot_both_succeed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            processes = []
            for variant in ("first", "second"):
                cmd, env = self.command(root, variant, True)
                processes.append(
                    subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                )
            statuses = []
            for process in processes:
                output, error = process.communicate(timeout=10)
                self.assertIn(process.returncode, (0, 3), output + error)
                statuses.append(process.returncode)
            self.assertEqual(sorted(statuses), [0, 3])

    def test_same_identity_resumes_and_different_identity_is_rejected_control(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for variant, expected in (("first", 0), ("first", 0), ("second", 3)):
                cmd, env = self.command(root, variant)
                result = subprocess.run(cmd, env=env, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)


if __name__ == "__main__":
    sys.exit(claim(Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1"))
