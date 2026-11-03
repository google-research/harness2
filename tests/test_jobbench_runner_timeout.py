# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The JobBench runner owns its wall limit, so it does not need GNU timeout."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks import jb_opencode_runner as runner


class RunnerTimeoutTests(unittest.TestCase):
    def settings(self, root: Path, *, timeout: str = "2") -> runner.Settings:
        return runner.Settings(
            task_ids=("task1",),
            label="jb-oc-agg1__task1__base1",
            render_dir=root / "render",
            tasks_base=root / "tasks",
            opencode_dir=root / "opencode",
            model_id="provider/model",
            agent_name="",
            variant="",
            agent_steps="",
            timeout_per_task=timeout,
            max_retries=1,
            log_dir=root / "logs",
        )

    def test_command_does_not_wrap_bun_in_external_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = runner._command(self.settings(root), root / "workspace", "prompt", "title")
        self.assertEqual(command[0], "bun")
        self.assertNotIn("--kill-after=30s", command)

    def test_timeout_after_sigterm_returns_124(self):
        started = time.monotonic()
        code = runner._run_process(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=Path.cwd(),
            env=None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout_s=0.2,
            grace_s=0.5,
        )
        self.assertEqual(code, 124)
        self.assertLess(time.monotonic() - started, 5)

    def test_sigterm_ignoring_process_is_killed_and_returns_137(self):
        script = (
            "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n"
        )
        started = time.monotonic()
        code = runner._run_process(
            [sys.executable, "-c", script],
            cwd=Path.cwd(),
            env=None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout_s=1.0,
            grace_s=0.5,
        )
        self.assertEqual(code, 137)
        self.assertLess(time.monotonic() - started, 5)

    def test_clean_exit_status_is_preserved(self):
        code = runner._run_process(
            [sys.executable, "-c", "raise SystemExit(7)"],
            cwd=Path.cwd(),
            env=None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout_s=5,
        )
        self.assertEqual(code, 7)

    def test_missing_executor_returns_127_and_logs_the_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stderr = root / "stderr.log"
            with stderr.open("w", encoding="utf-8") as stream:
                code = runner._run_process(
                    [str(root / "no-such-executable")],
                    cwd=root,
                    env=None,
                    stdout=subprocess.DEVNULL,
                    stderr=stream,
                    timeout_s=1,
                )
            self.assertEqual(code, 127)
            self.assertIn("could not start", stderr.read_text(encoding="utf-8"))

    def test_attempt_applies_the_limit_and_keeps_the_event_stream(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary_dir = root / "bin"
            binary_dir.mkdir()
            fake_bun = binary_dir / "bun"
            fake_bun.write_text(
                "#!/usr/bin/env python3\n"
                "import json, time\n"
                "print(json.dumps({'type': 'text', 'part': {'type': 'text', "
                "'text': 'hello'}}), flush=True)\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            fake_bun.chmod(0o755)
            cfg = self.settings(root, timeout="1.0")
            cfg.opencode_dir.mkdir()
            cfg.log_dir.mkdir()
            trajectory = root / "trajectory.jsonl"
            log = cfg.log_dir / "attempt.log"
            with patch.dict(os.environ, {"PATH": f"{binary_dir}{os.pathsep}{os.environ['PATH']}"}):
                code = runner._attempt(cfg, root / "workspace", trajectory, log, "task1", 1)
            self.assertEqual(code, 124)
            self.assertIn('"type": "text"', trajectory.read_text(encoding="utf-8"))

    def test_timeout_zero_means_no_limit(self):
        self.assertIsNone(runner._timeout_seconds("0"))
        self.assertEqual(runner._timeout_seconds("12.5"), 12.5)
        with self.assertRaises(SystemExit):
            runner._timeout_seconds("soon")


if __name__ == "__main__":
    unittest.main()
