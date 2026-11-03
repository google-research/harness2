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

"""The spend summarise() records per (mode, k) must be visible in each summary line."""

import unittest

from harness2.core import report
from harness2.core.judge import Scored


class SummaryLineSpendTests(unittest.TestCase):
    def test_line_shows_spend_when_present(self):
        s = report.Summary(mode="agg", k=2, n=3, score=50.0, all_pass=0.0, usd=1.25)
        self.assertIn("spend $1.25", s.line())

    def test_line_omits_spend_when_zero(self):
        s = report.Summary(mode="seq", k=1, n=2, score=66.67, all_pass=0.0)
        self.assertNotIn("spend", s.line())

    def test_line_shows_spend_even_without_graded_rows(self):
        s = report.Summary(mode="agg", k=2, usd=0.75)
        line = s.line()
        self.assertIn("no graded task", line)
        self.assertIn("spend $0.75", line)

    def test_line_without_graded_rows_omits_zero_spend(self):
        s = report.Summary(mode="seq", k=1)
        self.assertNotIn("spend", s.line())

    def test_line_shows_spend_with_lab_figures(self):
        s = report.Summary(mode="agg", k=2, n=3, score=50.0, all_pass=25.0, usd=1.25)
        line = s.line(bench="lab")
        self.assertIn("partial  50.00% | pass  25.0%", line)
        self.assertIn("spend $1.25", line)

    def test_summarise_spend_reaches_rendered_line(self):
        rows = [
            Scored(
                task_id="t1",
                mode="agg",
                k=2,
                label="agg0",
                run_id="r1",
                chosen_is_base=False,
                n_passed=1,
                n_criteria=2,
            ),
            Scored(
                task_id="t2",
                mode="agg",
                k=2,
                label="agg1",
                run_id="r2",
                chosen_is_base=False,
                n_passed=1,
                n_criteria=2,
            ),
        ]
        summaries = report.summarise(rows, usd={("agg", 2): 1.25})
        rendered = report.render(summaries, title="spend case", bench="jb")
        self.assertIn("spend $1.25", rendered)

    def test_summarise_zero_spend_is_not_rendered(self):
        rows = [
            Scored(
                task_id="t1",
                mode="agg",
                k=2,
                label="agg0",
                run_id="r1",
                chosen_is_base=False,
                n_passed=2,
                n_criteria=2,
            ),
        ]
        summaries = report.summarise(rows, usd={("agg", 2): 0.0})
        rendered = report.render(summaries, title="zero spend case", bench="jb")
        self.assertNotIn("spend $", rendered)


if __name__ == "__main__":
    unittest.main()
