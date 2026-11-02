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

"""Hardening tests for reflection tripwires and error propagation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core import context, selection
from harness2.core.proposer import Reply
from harness2.core.selection import Selection
from harness2.core.spec import RunView
from harness2.demo import DemoAdapter, DemoProposer


class ReflectionHardeningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.prop = DemoProposer(self.adapter.spec.channels["proposer"])

    def test_distil_rejects_verdict_markers_without_raising_value_error(self):
        sel = Selection(task_id="task-1", mode="agg", k=1, chosen_label="c1")
        sel.why = "c1 was chosen."
        poisoned_reply = Reply(
            label="experience.agg1",
            text=(
                "<experience>\n"
                "cue: general cue\n"
                "lesson: this lesson claims deterministic_eval_result was positive\n"
                "evidence: execution showed success\n"
                "confidence: high\n"
                "</experience>"
            ),
            ok=True,
            cost_usd=0.01,
            attempts=1,
        )
        refl_dir = self.root / "reflection"
        refl_dir.mkdir(parents=True)
        with patch.object(self.prop, "call", return_value=poisoned_reply):
            exp = selection._distil(
                "task-1",
                sel,
                {},
                adapter=self.adapter,
                prop=self.prop,
                root=refl_dir,
            )
        self.assertEqual(exp, {})

    def test_reflect_adoption_captures_value_error_and_preserves_run(self):
        sel = Selection(task_id="task-1", mode="agg", k=1, chosen_label="c1", ok=True)
        sel.why = "chosen candidate"
        harness_dir = self.root / "harnesses" / "c1"
        harness_dir.mkdir(parents=True)
        (harness_dir / "scripts").mkdir()
        (harness_dir / "scripts" / "summarize.py").write_text("print('ok')")

        run_dir = self.root / "runs" / "c1"
        run_dir.mkdir(parents=True)
        (run_dir / "answer.txt").write_text("Total: 85.15\n")

        candidates = {
            "c1": RunView(
                label="c1",
                run_id="run-1",
                run_dir=run_dir,
                ok=True,
                delivered=True,
            )
        }

        # Simulate context tripwire raising ValueError
        with patch.object(
            context, "assert_no_grader_verdicts", side_effect=ValueError("grader verdict leak")
        ):
            exp = selection.reflect_adoption(
                "task-1",
                sel,
                candidates,
                adapter=self.adapter,
                prop=self.prop,
                work_dir=self.root,
            )

        self.assertEqual(exp, {})
        self.assertIn("reflection rejected", sel.error)


if __name__ == "__main__":
    unittest.main()
