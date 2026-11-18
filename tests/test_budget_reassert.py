# Copyright 2026 Google LLC
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

"""The persisted ledger's budget identity is re-asserted on read (#143, ask 1c).

`assert_identity` runs at the end of a mode and never sees an interrupted run;
`harness2-judge` and `harness2-report` re-check what the run actually wrote, so a
truncated or oddly-resumed task is named instead of scored as if it had completed.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from harness2.cli.judge import collect_selections
from harness2.cli.report import _spend_and_fallbacks
from harness2.core.schedule import budget_violations


def summary(ledger: dict | None, *, arms: bool = False) -> dict:
    mode_record = {"ledger": ledger} if ledger is not None else {}
    body = {
        "task_id": "contracts/task-1",
        "selections": {"agg": {"task_id": "contracts/task-1", "mode": "agg", "ok": True,
                               "chosen_label": "agg1"}},
    }
    body["arms" if arms else "modes"] = {"agg": mode_record}
    return body


def complete_ledger() -> dict:
    return {"R": 4, "expected_R": 4, "P": 3, "expected_P": 3}


class BudgetViolationsTests(unittest.TestCase):
    def test_complete_and_missing_ledgers_assert_nothing(self):
        self.assertEqual(budget_violations(summary(complete_ledger()), ("agg", "seq")), {})
        # a legacy record with no ledger keeps the check off rather than failing
        self.assertEqual(budget_violations(summary(None), ("agg",)), {})

    def test_truncated_ledger_names_the_mode_and_counts(self):
        broken = {"R": 2, "expected_R": 4, "P": 3, "expected_P": 3}
        out = budget_violations(summary(broken), ("agg",))
        self.assertEqual(list(out), ["agg"])
        self.assertIn("R 2/4", out["agg"])
        self.assertIn("re-run the task", out["agg"])

    def test_legacy_arms_key_is_read(self):
        broken = {"R": 4, "expected_R": 4, "P": 1, "expected_P": 3}
        out = budget_violations(summary(broken, arms=True), ("agg",))
        self.assertIn("P 1/3", out["agg"])


class ReadSideTests(unittest.TestCase):
    def test_judge_skips_a_truncated_task_instead_of_judging_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            ns_dir = Path(temporary)
            task = ns_dir / "contracts__task-1"
            task.mkdir(parents=True)
            good = summary(complete_ledger())
            good["task_id"] = "contracts/task-2"
            good["selections"]["agg"]["task_id"] = "contracts/task-2"
            (ns_dir / "contracts__task-2").mkdir()
            (ns_dir / "contracts__task-2" / "summary.json").write_text(json.dumps(good))
            (task / "summary.json").write_text(
                json.dumps(summary({"R": 2, "expected_R": 4, "P": 3, "expected_P": 3}))
            )
            out = io.StringIO()
            with redirect_stdout(out):
                selections = collect_selections(ns_dir, ("agg",))
        self.assertEqual([s["task_id"] for s in selections], ["contracts/task-2"])
        self.assertIn("budget identity violated, not judging", out.getvalue())
        self.assertIn("R 2/4", out.getvalue())

    def test_report_carries_the_violation_next_to_its_figures(self):
        with tempfile.TemporaryDirectory() as temporary:
            ns_dir = Path(temporary)
            task = ns_dir / "contracts__task-1"
            task.mkdir(parents=True)
            (task / "summary.json").write_text(
                json.dumps(summary({"R": 4, "expected_R": 4, "P": 2, "expected_P": 3}))
            )
            fell_back, usd, notes = _spend_and_fallbacks(ns_dir, ("agg",), 1)
        self.assertEqual(fell_back, {("agg", 1): 0})
        self.assertEqual(len(notes), 1)
        self.assertIn("P 2/3", notes[0])


if __name__ == "__main__":
    unittest.main()
