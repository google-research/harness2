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

"""Regressions for what judge_one accepts from an adapter's score payload.

The judge owes its callers two things: a payload that omits a field must not be read as a
zero, and a payload that cannot be graded must be excluded rather than raising later in
the report. Both cases are driven through judge_one and summarise, i.e. what
harness2-judge and harness2-report do, with no model calls and no network.
"""

import unittest
from pathlib import Path

from harness2.core.judge import judge_one
from harness2.core.report import render, summarise
from harness2.core.spec import BenchmarkAdapter


class PayloadAdapter(BenchmarkAdapter):
    """An adapter that returns a fixed payload, like LAB forwards its scores.json."""

    def __init__(self, payload: dict):
        self.payload = payload

    def score(self, run_dir: Path, task_id: str) -> dict:
        return self.payload


def lab_payload(upstream: dict) -> dict:
    """lab.py forwards every key it reads, so an absent key arrives as None."""
    return {key: upstream.get(key) for key in ("n_passed", "n_criteria", "all_pass")}


def judge(payload: dict):
    return judge_one(PayloadAdapter(payload), "ledger-1", "agg", 1, "agg1", "r1", Path("/tmp/x"))


class JudgePayloadTrustTest(unittest.TestCase):
    def test_explicit_null_all_pass_falls_back_to_the_derivation(self) -> None:
        row = judge(lab_payload({"n_passed": 4, "n_criteria": 4}))

        self.assertTrue(row.graded)
        self.assertEqual(row.frac, 1.0)
        self.assertTrue(row.all_pass, "a complete pass must not read as a failed pass")

    def test_explicit_all_pass_still_wins(self) -> None:
        self.assertTrue(
            judge(lab_payload({"n_passed": 3, "n_criteria": 4, "all_pass": True})).all_pass
        )
        self.assertFalse(
            judge(lab_payload({"n_passed": 4, "n_criteria": 4, "all_pass": False})).all_pass
        )

    def test_missing_n_passed_is_ungraded_not_a_crash(self) -> None:
        row = judge(lab_payload({"n_criteria": 4, "all_pass": True}))

        self.assertFalse(row.graded)
        self.assertIsNone(row.frac)
        self.assertEqual(row.error, "judge payload omitted n_passed")
        summary = summarise([row])[0]
        self.assertEqual(summary.n, 0)
        self.assertEqual(summary.n_ungraded, 1)

    def test_report_renders_a_partial_payload_instead_of_raising(self) -> None:
        row = judge(lab_payload({"n_criteria": 4, "all_pass": True}))

        line = render([summarise([row])[0]], bench="lab")

        self.assertIn("no graded task", line)

    def test_passing_task_reports_a_hundred_percent_pass_rate(self) -> None:
        row = judge(lab_payload({"n_passed": 4, "n_criteria": 4}))

        line = render([summarise([row])[0]], bench="lab")

        self.assertIn("pass 100.0%", line)
        self.assertIn("partial 100.00%", line)


if __name__ == "__main__":
    unittest.main()
