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

"""Ungraded task counts remain visible even when no score can be reported."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.cli import _common
from harness2.cli import report as report_cli
from harness2.core import judge, report


def _row(task_id, *, mode="agg", n_passed=None, n_criteria=None):
    return judge.Scored(
        task_id=task_id,
        mode=mode,
        k=1,
        label="agg1" if mode == "agg" else "c1",
        run_id=f"{mode}/{task_id}",
        chosen_is_base=False,
        n_passed=n_passed,
        n_criteria=n_criteria,
    )


class UngradedReportTests(unittest.TestCase):
    def test_all_ungraded_counts_survive_rendering(self):
        for bench in ("jb", "lab"):
            for mode, count in (("agg", 1), ("seq", 3)):
                with self.subTest(bench=bench, mode=mode, count=count):
                    rows = [_row(f"task-{i}", mode=mode) for i in range(count)]
                    summaries = report.summarise(rows)
                    self.assertEqual((summaries[0].n, summaries[0].n_ungraded), (0, count))
                    self.assertIsNone(summaries[0].score)
                    self.assertIsNone(summaries[0].all_pass)
                    rendered = report.render(summaries, bench=bench)
                    self.assertIn(f"no graded task | ungraded {count}", rendered)
                    self.assertNotIn("%", rendered)

    def test_mixed_grades_exclude_ungraded_tasks_from_score(self):
        rows = [_row("graded", n_passed=1, n_criteria=2)]
        rows.extend(_row(f"ungraded-{i}") for i in range(3))
        summaries = report.summarise(rows)
        self.assertEqual((summaries[0].n, summaries[0].n_ungraded), (1, 3))
        self.assertEqual(summaries[0].score, 50.0)
        rendered = report.render(summaries, bench="jb")
        self.assertIn("50.00%", rendered)
        self.assertIn("ungraded 3", rendered)

    def test_all_graded_tasks_omit_ungraded_count(self):
        rows = [
            _row("partial", n_passed=1, n_criteria=2),
            _row("complete", n_passed=2, n_criteria=2),
        ]
        summaries = report.summarise(rows)
        self.assertEqual((summaries[0].n, summaries[0].n_ungraded), (2, 0))
        self.assertEqual(summaries[0].score, 75.0)
        rendered = report.render(summaries, bench="jb")
        self.assertIn("75.00%", rendered)
        self.assertNotIn("ungraded", rendered)

    def test_empty_summary_does_not_invent_ungraded_tasks(self):
        summary = report.Summary(mode="agg", k=1)
        self.assertEqual(summary.line(), "  parallel k=1: no graded task")

    def test_report_cli_retains_all_ungraded_count(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            config, "RUNS", Path(temporary)
        ):
            adapter = _common.make_adapter("lab", None, "oc")
            namespace = _common.runs_root_for(adapter) / _common.summary_namespace(
                adapter, 1, seed=None
            )
            judge.write([_row(f"task-{i}") for i in range(3)], namespace / "judged.json")
            output = io.StringIO()
            with redirect_stdout(output):
                status = report_cli.main(["--bench", "lab", "--mode", "parallel", "--k", "1"])
            self.assertEqual(status, 0)
            self.assertIn("parallel k=1: no graded task | ungraded 3", output.getvalue())
            self.assertNotIn("%", output.getvalue())


if __name__ == "__main__":
    unittest.main()
