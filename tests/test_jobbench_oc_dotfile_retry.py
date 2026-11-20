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
from unittest.mock import patch

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


    def _retry_after_first_output(self, first_output: str, *, directory_only: bool = False):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cfg = self._settings(base)
            calls = []

            def fake_attempt(cfg, workspace, traj_file, log_file, task_name, attempt):
                calls.append(attempt)
                output = Path(workspace) / "output"
                if attempt == 1:
                    target = output / first_output
                    if directory_only:
                        target.mkdir(parents=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text("cache data", encoding="utf-8")
                else:
                    (output / "report.txt").write_text("the deliverable", encoding="utf-8")
                return 0

            with patch.object(R, "TEMP_BASE", base / "workspaces"), patch.object(
                R, "_attempt", fake_attempt
            ):
                delivered = R.run_one(cfg, "proftask")
            self.assertEqual(calls, [1, 2])
            self.assertTrue(delivered)
            final = cfg.tasks_base / "proftask" / "model_output" / "L1"
            self.assertEqual(sorted(p.name for p in final.iterdir()), ["report.txt"])
            self.assertEqual((final / "report.txt").read_text(), "the deliverable")

    def test_empty_directory_retries_then_delivers(self):
        self._retry_after_first_output("results", directory_only=True)

    def test_hidden_nested_file_retries_then_delivers(self):
        self._retry_after_first_output("results/.cache")

    def test_visible_file_under_hidden_directory_retries_then_delivers(self):
        self._retry_after_first_output("results/.cache/report.txt")

    def test_nested_visible_file_delivers_without_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cfg = self._settings(base)
            calls = []

            def fake_attempt(cfg, workspace, traj_file, log_file, task_name, attempt):
                calls.append(attempt)
                target = Path(workspace) / "output" / "results" / "nested" / "report.txt"
                target.parent.mkdir(parents=True)
                target.write_text("nested deliverable", encoding="utf-8")
                return 0

            with patch.object(R, "TEMP_BASE", base / "workspaces"), patch.object(
                R, "_attempt", fake_attempt
            ):
                delivered = R.run_one(cfg, "proftask")
            self.assertTrue(delivered)
            self.assertEqual(calls, [1])
            final = cfg.tasks_base / "proftask" / "model_output" / "L1"
            self.assertEqual((final / "results/nested/report.txt").read_text(), "nested deliverable")


if __name__ == "__main__":
    unittest.main()
