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

"""A judge that produced no total is UNGRADED, not a graded zero.

`core.judge` states the contract: "A judge failure is None, never a zero, and a None
is excluded from the denominator." A details payload that carried a max_score but no
usable total used to be graded at 0.0 -- a failure counted as a score.
"""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.jobbench import JobBenchAdapter, _parse_details


def _details(total=None, passed=3, counted=3, max_score=5.0):
    got = {
        "total_count": 1,
        "rubrics": [{"result": {"criteria_count": counted, "criteria_passed": passed}}],
        "max_score": max_score,
    }
    if total is not None:
        got["total_score"] = total
    return got


class ParseDetailsTests(unittest.TestCase):
    def test_a_payload_without_a_total_is_ungraded(self):
        self.assertIsNone(_parse_details(_details()))

    def test_a_payload_that_contradicts_itself_is_ungraded(self):
        # every criterion passed, so the weighted total cannot be zero
        self.assertIsNone(_parse_details(_details(total=0.0, passed=3, counted=3)))

    def test_a_genuine_zero_is_still_graded(self):
        parsed = _parse_details(_details(total=0.0, passed=0, counted=3))
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed["n_passed"], parsed["n_criteria"]), (0.0, 5.0))
        self.assertFalse(parsed["all_pass"])
        self.assertEqual(parsed["metric"], "weighted")

    def test_a_real_score_is_graded(self):
        parsed = _parse_details(_details(total=3.5, passed=2, counted=3))
        self.assertEqual((parsed["n_passed"], parsed["n_criteria"]), (3.5, 5.0))
        self.assertFalse(parsed["all_pass"])
        self.assertEqual(parsed["criteria_passed"], 2)
        full = _parse_details(_details(total=5.0, passed=3, counted=3))
        self.assertTrue(full["all_pass"])

    def test_the_unweighted_fallback_is_unchanged(self):
        got = {
            "rubrics": [{"result": {"criteria_count": 3, "criteria_passed": 2}}],
        }
        parsed = _parse_details(got)
        self.assertEqual(parsed["metric"], "unweighted-fallback")
        self.assertEqual((parsed["n_passed"], parsed["n_criteria"]), (2, 3))
        self.assertFalse(parsed["all_pass"])

    def test_a_details_file_that_is_not_a_grade_is_quarantined(self):
        # readable, but the judge never produced a total: put it aside so the judge
        # runs again instead of this file being served as a zero forever.
        adapter = JobBenchAdapter(substrate="oc")
        with tempfile.TemporaryDirectory() as td:
            details = Path(td) / "judge.json"
            details.write_text(json.dumps(_details()))
            self.assertIsNone(adapter._read_details(details))
            self.assertFalse(details.exists())
            self.assertTrue(details.with_name("judge.json.judge_failed").exists())

    def test_a_details_file_that_is_a_grade_is_served(self):
        adapter = JobBenchAdapter(substrate="oc")
        with tempfile.TemporaryDirectory() as td:
            details = Path(td) / "judge.json"
            details.write_text(json.dumps(_details(total=3.5, passed=2, counted=3)))
            self.assertIsNotNone(adapter._read_details(details))
            self.assertTrue(details.exists())


if __name__ == "__main__":
    unittest.main()
