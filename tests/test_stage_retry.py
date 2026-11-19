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

"""A retried proposal starts from its previous attempt, whatever rejected that attempt."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core import recursion, tree
from harness2.core.proposer import Proposer
from harness2.demo import DemoAdapter


class StageRetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)

    def run_stage(self, defect: str, extra: str):
        """Through the real Proposer: attempt 1 fixes the script and adds `defect`, the retry
        only drops `defect` from whatever it finds in `context/harness/`."""
        seen, missions = [], []

        def session(cwd, mission, system, wall):
            harness = Path(cwd) / "context/harness"
            script = harness / "scripts/summarize.py"
            seen.append(script.read_text())
            missions.append(mission)
            if "PREVIOUS ATTEMPT WAS REJECTED" not in mission:
                (harness / defect).write_text(extra)
                script.write_text(script.read_text().replace(" if amount > 0", ""))
            elif (harness / defect).is_file():
                (harness / defect).unlink()
            return "<notes>Keep signed amounts.</notes>", 0.0, ""

        work = self.root / defect.replace("/", "_")
        prop = Proposer(self.adapter.spec.channels["proposer"], audit_dir=work / "audit")
        with patch.object(prop, "_opencode", side_effect=session):
            stage = recursion._run_stage(
                label="inv1",
                dim="INV",
                job="Repair the script",
                adapter=self.adapter,
                prop=prop,
                ctx_dir=work / "context",
                task_id="ledger-1",
                rollouts=[],
                fallback=tree.read_tree(
                    self.adapter.substrate.surface(), self.adapter.base_harness()
                ),
                harness_dir=self.adapter.base_harness(),
                library_dir=None,
            )
        return stage, seen, missions

    def test_retry_keeps_the_rejected_attempts_edits(self):
        for defect, extra in (
            ("scripts/check.py", "def broken(:\n"),
            ("scripts/summarize.py.bak", "# an editor's backup, off the editable surface\n"),
        ):
            with self.subTest(defect=defect):
                stage, seen, missions = self.run_stage(defect, extra)
                self.assertIn(defect, missions[1])
                self.assertNotIn(" if amount > 0", seen[1])
                self.assertEqual(
                    (stage.fell_back, stage.kept_base, stage.attempts), (False, False, 2)
                )
                self.assertNotIn(" if amount > 0", stage.tree["scripts/summarize.py"])
                self.assertNotIn(defect, stage.tree)


if __name__ == "__main__":
    unittest.main()
