# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Offline checks of which cached selections the public report aggregates."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harness2.cli import _common, judge, report
from harness2.core import schedule
from harness2.core.judge import Scored, write
from harness2.demo import DemoAdapter, DemoProposer


class _ScoredDemo(DemoAdapter):
    def score(self, run_dir, task_id):
        amount = float(self.deliverable_text(run_dir).removeprefix("Total: "))
        return {"n_passed": int(amount >= 20), "n_criteria": 1}


class ReportSelectionIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.adapter = _ScoredDemo(Path(temporary.name))
        self.prop = DemoProposer(self.adapter.spec.channels["proposer"])
        self.runs = _common.runs_root_for(self.adapter)
        self.ns_dir = self.runs / "demo-oc-agg1"
        self.argv = ["--bench", "lab", "--k", "1", "--modes", "parallel,sequential"]
        self.enterContext(patch.object(_common, "make_adapter", return_value=self.adapter))
        self.enterContext(patch.object(judge.config, "validate_models"))
        self.enterContext(redirect_stdout(io.StringIO()))

    def run_task(self, task_id, modes=schedule.MODES):
        result = schedule.run_task(
            task_id, 1, adapter=self.adapter, prop=self.prop, runs_root=self.runs, modes=modes
        )
        self.assertEqual(result.status, "ok", result.errors)

    def assert_scores(self, parallel, sequential):
        printed = io.StringIO()
        with redirect_stdout(printed):
            self.assertEqual(report.main(self.argv), 0)
        self.assertIn(f"parallel k=1: {parallel}", printed.getvalue())
        self.assertIn(f"sequential k=1: {sequential}", printed.getvalue())

    def test_correcting_one_task_invalidates_only_its_previous_selections(self):
        for task in self.adapter.tasks():
            self.run_task(task)
        self.assertEqual(judge.main(self.argv), 0)
        self.assert_scores("n=  2 | score 100.00%", "n=  2 | score 100.00%")

        task_id = "ledger-1"
        (self.adapter.task_files(task_id) / "ledger.csv").write_text("amount\n1.00\n2.00\n")
        self.run_task(task_id, modes=("agg",))
        summary = json.loads((self.ns_dir / task_id / "summary.json").read_text())
        self.assertEqual(set(summary["selections"]), {"agg"})

        judged = self.ns_dir / "judged.json"
        cached_before_report = judged.read_bytes()
        self.assert_scores("n=  1 | score 100.00%", "n=  1 | score 100.00%")
        self.assertEqual(judged.read_bytes(), cached_before_report)

        self.assertEqual(judge.main(self.argv), 0)
        self.assert_scores("n=  2 | score  50.00%", "n=  1 | score 100.00%")

        self.run_task(task_id, modes=("seq",))
        self.assertEqual(judge.main(self.argv), 0)
        self.assert_scores("n=  2 | score  50.00%", "n=  2 | score  50.00%")

    def test_judged_only_snapshot_can_still_be_reported(self):
        rows = [
            Scored(
                task_id="ledger-1",
                mode=mode,
                k=1,
                label=label,
                run_id=label,
                chosen_is_base=False,
                n_passed=1,
                n_criteria=1,
            )
            for mode, label in (("agg", "agg1"), ("seq", "c1"))
        ]
        write(rows, self.ns_dir / "judged.json")
        self.assert_scores("n=  1 | score 100.00%", "n=  1 | score 100.00%")


if __name__ == "__main__":
    unittest.main()
