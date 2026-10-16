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

"""Keep verifier fractions intact through the real score cache and report CLI."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks.workbuddy import WbAdapter
from harness2.cli import _common as C
from harness2.core import judge


class WorkbuddyScorePrecisionTests(unittest.TestCase):
    def report_from_verifier(self, payload):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        runs = root / "runs"
        with patch.object(config, "RUNS", runs):
            adapter = WbAdapter("office")
        trial = root / "trial"
        artifact = trial / "verifier/raw_artifacts/report.txt"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("The report contains the requested analysis.\n" * 10)
        self.assertTrue(adapter.delivered(trial))
        (trial / "verifier/score.json").write_text(json.dumps(payload))
        scored = judge.judge_one(adapter, "task1", "agg", 1, "agg1", "trial", trial)
        output = C.runs_root_for(adapter) / C.summary_namespace(adapter, 1, seed=None)
        judge.write([scored], output / "judged.json")
        result = subprocess.run(
            [sys.executable, "-m", "harness2.cli.report", "--bench", "wb", "--subset", "office"],
            env={**os.environ, "HARNESS2_RUNS_DIR": str(runs)},
            text=True,
            capture_output=True,
            check=True,
        )
        return scored, result.stdout

    def test_test_count_fraction_reaches_report_without_quantization(self):
        scored, report = self.report_from_verifier({"tests_passed": 1, "tests_total": 3})
        self.assertIn("score  33.33%", report)
        self.assertAlmostEqual(scored.frac, 1 / 3)

    def test_decimal_reward_reaches_report_without_quantization(self):
        scored, report = self.report_from_verifier({"reward": 0.8847})
        self.assertIn("score  88.47%", report)
        self.assertAlmostEqual(scored.frac, 0.8847)

    def test_zero_and_full_credit_controls(self):
        for reward in (0, 1):
            with self.subTest(reward=reward):
                scored, report = self.report_from_verifier({"reward": reward})
                self.assertTrue(scored.graded)
                self.assertEqual(scored.frac, reward)
                self.assertEqual(scored.all_pass, reward == 1)
                self.assertIn(f"score {reward * 100:6.2f}%", report)


if __name__ == "__main__":
    unittest.main()
