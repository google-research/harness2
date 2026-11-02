# SPDX-License-Identifier: Apache-2.0
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

"""Real judged-cache writer -> report CLI, including ordinary judge failures."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness2.core.judge import Scored, write


class ReportCoverageTests(unittest.TestCase):
    def report(self, scores):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            for mode, tasks in scores.items():
                for task, score in tasks.items():
                    rows.append(
                        Scored(
                            task_id=f"accounting/task{task}",
                            mode=mode,
                            k=1,
                            label="agg1" if mode == "agg" else "c1",
                            run_id=f"{task}-{mode}",
                            chosen_is_base=False,
                            n_passed=score,
                            n_criteria=1 if score is not None else None,
                            all_pass=score == 1,
                            error="judge unavailable" if score is None else "",
                        )
                    )
            write(rows, root / "jb/adapt/jb-oc-agg1/judged.json")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "harness2.cli.report",
                    "--bench",
                    "jb",
                    "--k",
                    "1",
                    "--mode",
                    "parallel,sequential",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=dict(os.environ, HARNESS2_RUNS_DIR=str(root)),
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout

    def test_matched_graded_tasks_keep_the_existing_noise_note(self):
        output = self.report({"agg": {1: 1, 2: 0}, "seq": {1: 1, 2: 0}})
        self.assertIn("NOISE FLOOR", output)
        self.assertNotIn("different graded task sets", output)

    def test_equal_counts_with_different_tasks_are_not_a_noise_floor(self):
        output = self.report({"agg": {1: 1, 2: None}, "seq": {1: None, 2: 0}})
        self.assertNotIn("NOISE FLOOR", output)
        self.assertIn("different graded task sets", output)
        self.assertIn("ungraded 1", output)

    def test_missing_grade_changes_coverage_without_changing_scores(self):
        output = self.report({"agg": {1: 1, 2: 0}, "seq": {1: 1, 2: None}})
        self.assertNotIn("NOISE FLOOR", output)
        self.assertIn("different graded task sets", output)
        self.assertIn("score  50.00%", output)
        self.assertIn("score 100.00%", output)
