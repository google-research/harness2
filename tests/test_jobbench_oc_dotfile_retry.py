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

"""A rollout that writes only a hidden file must retry and not be called delivered."""

import shutil
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks import jb_opencode_runner as R


class DotfileRetryTests(unittest.TestCase):
    def _settings(self, base: Path, *, max_retries: int = 2) -> R.Settings:
        render_dir = base / "render"
        (render_dir / ".opencode").mkdir(parents=True)
        tasks_base = base / "store"
        (tasks_base / "proftask" / "task_folder").mkdir(parents=True)
        log_dir = base / "logs"
        log_dir.mkdir()
        return R.Settings(
            task_ids=("proftask",),
            label="L1",
            render_dir=render_dir,
            tasks_base=tasks_base,
            opencode_dir=base / "oc",
            model_id="m",
            agent_name="",
            variant="",
            agent_steps="",
            timeout_per_task="3600",
            max_retries=max_retries,
            log_dir=log_dir,
        )

    def test_hidden_only_output_retries_then_delivers(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        cfg = self._settings(base)

        calls: list[int] = []

        def fake_attempt(cfg, workspace, traj_file, log_file, task_name, attempt):
            calls.append(attempt)
            out = Path(workspace) / "output"
            if attempt == 1:
                (out / ".cache").write_text("junk", encoding="utf-8")  # hidden only
            else:
                (out / "report.txt").write_text("the deliverable", encoding="utf-8")
            return 0

        self.addCleanup(shutil.rmtree, R.TEMP_BASE / f"proftask_L1_{__import__('os').getpid()}", True)
        orig = R._attempt
        R._attempt = fake_attempt
        try:
            delivered = R.run_one(cfg, "proftask")
        finally:
            R._attempt = orig

        # The hidden-only first attempt is not a deliverable, so the loop must try again.
        self.assertEqual(calls, [1, 2])
        self.assertTrue(delivered)
        final = cfg.tasks_base / "proftask" / "model_output" / "L1"
        self.assertEqual(sorted(p.name for p in final.iterdir()), ["report.txt"])

    def test_hidden_only_everywhere_is_not_delivered(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        cfg = self._settings(base)

        def fake_attempt(cfg, workspace, traj_file, log_file, task_name, attempt):
            (Path(workspace) / "output" / ".cache").write_text("junk", encoding="utf-8")
            return 0

        self.addCleanup(shutil.rmtree, R.TEMP_BASE / f"proftask_L1_{__import__('os').getpid()}", True)
        orig = R._attempt
        R._attempt = fake_attempt
        try:
            delivered = R.run_one(cfg, "proftask")
        finally:
            R._attempt = orig
        self.assertFalse(delivered)


if __name__ == "__main__":
    unittest.main()
