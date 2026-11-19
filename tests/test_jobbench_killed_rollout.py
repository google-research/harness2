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

"""A JobBench rollout that stops on a non-zero exit must say so in its trajectory."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks import jb_opencode_runner as runner
from harness2.substrate.opencode import render_events

FAKE_BUN = """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path

ws = Path(sys.argv[sys.argv.index("--dir") + 1])
first = sys.argv[sys.argv.index("--title") + 1].endswith("| a1")
mode = os.environ["FAKE_AGENT"]
if mode == "flaky" and first:
    sys.exit(1)
(ws / "output" / "memo.docx").write_text("first draft")
event = json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "write", "state": {
    "status": "completed", "input": {"filePath": "output/memo.docx"}, "output": "written"}}})
sys.stdout.write(event if mode == "unterminated" else event + "\\n")
sys.stdout.flush()
if mode in ("slow", "unterminated"):
    time.sleep(30)
if mode == "crash":
    sys.exit(1)
print(json.dumps({"type": "step_finish", "part": {"type": "step-finish", "reason": "stop"}}))
"""


@unittest.skipUnless(shutil.which("timeout"), "the runner needs GNU timeout")
class KilledRolloutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "bun").write_text(FAKE_BUN)
        (bin_dir / "bun").chmod(0o755)
        self.tasks = self.root / "tasks"
        (self.tasks / "t1" / "task_folder").mkdir(parents=True)
        (self.tasks / "t1" / "task_folder" / "TASK_INSTRUCTIONS.txt").write_text("write a memo")
        (self.tasks / ".logs").mkdir()
        (self.root / "render" / ".opencode").mkdir(parents=True)
        (self.root / "oc" / "packages" / "opencode").mkdir(parents=True)
        (self.root / "hevo_ws").mkdir()
        patcher = patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        base_patch = patch.object(runner, "TEMP_BASE", self.root / "hevo_ws")
        base_patch.start()
        self.addCleanup(base_patch.stop)

    def _roll(self, agent: str, limit: str = "30") -> tuple[bool, list[Path]]:
        cfg = runner.Settings(
            task_ids=("t1",),
            label="base1",
            render_dir=self.root / "render",
            tasks_base=self.tasks,
            opencode_dir=self.root / "oc",
            model_id="fake/model",
            agent_name="",
            variant="",
            agent_steps="",
            timeout_per_task=limit,
            max_retries=2,
            log_dir=self.tasks / ".logs",
        )
        with patch.dict(os.environ, {"FAKE_AGENT": agent}):
            delivered = runner.run_one(cfg, "t1")
        return delivered, sorted((self.tasks / "t1" / "model_traj" / "base1").glob("*.jsonl"))

    def _log(self) -> str:
        return "".join(p.read_text() for p in (self.tasks / ".logs").glob("*.log"))

    @staticmethod
    def _rendered(traj: Path) -> list[str]:
        return render_events(traj, obs_trunc=1000, max_chars=10_000).splitlines()

    def test_rollout_stopped_at_the_time_limit_says_so(self):
        delivered, (traj,) = self._roll("slow", limit="1")

        self.assertTrue(delivered)
        last = self._rendered(traj)[-1]
        self.assertTrue(last.startswith("[ERROR] "), last)
        self.assertIn("stopped at the 1 s time limit (exit 124)", last)
        self.assertIn("kept as the deliverable", last)
        self.assertIn("EXIT 124:", self._log())

    def test_note_survives_an_unterminated_last_event(self):
        delivered, (traj,) = self._roll("unterminated", limit="1")

        self.assertTrue(delivered)
        lines = self._rendered(traj)
        self.assertIn("[OBSERVATION] written", lines)
        self.assertTrue(lines[-1].startswith("[ERROR] "), lines)

    def test_crashed_rollout_says_so(self):
        delivered, (traj,) = self._roll("crash")

        self.assertTrue(delivered)
        last = self._rendered(traj)[-1]
        self.assertTrue(last.startswith("[ERROR] "), last)
        self.assertIn("exited with status 1", last)
        self.assertIn("EXIT 1:", self._log())

    def test_retried_attempt_keeps_its_own_note(self):
        delivered, (first, second) = self._roll("flaky")

        self.assertTrue(delivered)
        note = self._rendered(first)[-1]
        self.assertIn("exited with status 1", note)
        self.assertIn("output directory was empty", note)
        self.assertNotIn('"error"', second.read_text())

    def test_signal_wording(self):
        cases = {
            (-9, "2400"): "SIGKILL (-9), after the 2400 s time limit or by the system",
            (137, "2400"): "SIGKILL (137), after the 2400 s time limit or by the system",
            (-11, "2400"): "killed by signal 11",
            (124, "40m"): "stopped at the 40m time limit (exit 124)",
        }
        for (code, limit), expected in cases.items():
            with self.subTest(code=code, limit=limit):
                traj = self.root / f"t{code}.jsonl"
                traj.write_text("")
                runner._note_exit(traj, self.root / "x.log", code, limit=limit, kept=False)
                self.assertIn(expected, render_events(traj, obs_trunc=1000, max_chars=10_000))

    def test_clean_rollout_is_left_alone(self):
        delivered, (traj,) = self._roll("clean")

        self.assertTrue(delivered)
        self.assertNotIn('"error"', traj.read_text())
        self.assertNotIn("EXIT", self._log())


if __name__ == "__main__":
    unittest.main()
