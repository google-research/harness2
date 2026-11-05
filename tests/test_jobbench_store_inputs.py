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

"""A JobBench rollout answers the task as it is now, not the run store's first copy of it.

The launcher points the runner at the run store (`TASKS_BASE_DIR`), which `execute()` seeds
from the dataset. When the dataset task changes, `execute()` sees new task inputs and
re-rolls, so the store copy the runner reads must change with it.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks.jobbench import JobBenchAdapter

TASK = "accountant/task1"
RUN_ID = "jb-oc-agg1/accountant/task1/base1"


class StoreTaskInputsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = self.root / "jb/dataset/main" / TASK / "task_folder"
        self.task.mkdir(parents=True)
        self.instructions = self.task / "TASK_INSTRUCTIONS.txt"
        self.instructions.write_text("Reconcile the March ledger.")
        (self.task / "ledger.csv").write_text("amount\n10\n")
        self.adapter = JobBenchAdapter("oc", store_root=self.root / "store")
        self.rolls = 0
        for p in (
            patch.object(config, "jb_repo", return_value=self.root / "jb"),
            patch.object(tempfile, "tempdir", str(self.root)),
            patch.object(self.adapter.substrate, "preflight"),
            patch.object(self.adapter, "_run_runner", side_effect=self.runner),
        ):
            p.start()
            self.addCleanup(p.stop)

    def runner(self, runner, env, *, wall, label):
        """jb_opencode_runner's read and write paths, without OpenCode."""
        self.rolls += 1
        task = Path(env["TASKS_BASE_DIR"]) / env["TASK_IDS"]
        out = task / "model_output" / env["LABEL"]
        out.mkdir(parents=True, exist_ok=True)
        seen = {p.name: p.read_text() for p in (task / "task_folder").iterdir()}
        (out / "answer.json").write_text(json.dumps(seen))

    def answer(self) -> dict:
        with redirect_stdout(io.StringIO()):
            out = self.adapter.execute(TASK, None, RUN_ID)
        return json.loads((out / "answer.json").read_text())

    def test_changed_instructions_re_roll_on_the_changed_task(self):
        self.answer()
        self.instructions.write_text("Reconcile the April ledger.")
        self.assertEqual(self.answer()["TASK_INSTRUCTIONS.txt"], "Reconcile the April ledger.")
        self.assertEqual(self.rolls, 2)

    def test_removed_source_file_is_gone_from_the_re_roll(self):
        self.answer()
        (self.task / "ledger.csv").unlink()
        self.assertEqual(self.answer(), {"TASK_INSTRUCTIONS.txt": "Reconcile the March ledger."})
        self.assertEqual(self.rolls, 2)

    def test_unchanged_task_reuses_the_rollout(self):
        first = self.answer()
        self.assertEqual(self.answer(), first)
        self.assertEqual(self.rolls, 1)


if __name__ == "__main__":
    unittest.main()
