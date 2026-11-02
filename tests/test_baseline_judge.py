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

"""Actual summary producer -> LAB cached score reader -> judge CLI."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness2.core.schedule import Namespace, TaskResult, _write_summary
from harness2.core.selection import Selection


class BaselineJudgeTests(unittest.TestCase):
    def judge_cached(self, payload, *, bases=True):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "local-run"
            run.mkdir()
            (run / "scores.json").write_text(json.dumps(payload), encoding="utf-8")
            namespace = Namespace("lab", "oc", "agg", 1)
            ns_dir = root / "lab/adapt" / str(namespace)
            task_dir = namespace.task_dir(root / "lab/adapt", "contracts/task-1")
            task_dir.mkdir(parents=True)
            selected = Selection(
                task_id="contracts/task-1",
                mode="agg",
                k=1,
                chosen_label="agg1",
                chosen_run_id="adapted-1",
                ok=True,
            ).to_dict()
            selected["chosen_run_dir"] = str(run)
            result = TaskResult(
                task_id="contracts/task-1",
                domain="contracts",
                k=1,
                status="ok",
                modes={"agg": {"ok": True}},
                base=[{"label": "base1", "run_id": "reference-1", "run_dir": str(run)}],
                selections={"agg": selected},
            )
            _write_summary(task_dir / "summary.json", result)
            env = {
                key: os.environ[key]
                for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "TERM")
                if key in os.environ
            }
            env.update(
                HARNESS2_RUNS_DIR=str(root),
                HARNESS2_SOLVER_MODEL="fixture/executor",
                HARNESS2_JUDGE_MODEL="fixture-judge",
                PYTHONDONTWRITEBYTECODE="1",
            )
            command = [
                sys.executable,
                "-m",
                "harness2.cli.judge",
                "--bench",
                "lab",
                "--k",
                "1",
                "--mode",
                "parallel",
            ]
            if bases:
                command.append("--bases")
            process = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            cache = ns_dir / ("base_judged.json" if bases else "judged.json")
            return process, json.loads(
                cache.read_text(encoding="utf-8")
            ) if cache.is_file() else None

    def test_missing_or_nonpositive_criteria_are_ungraded(self):
        for criteria in (None, 0, -1):
            with self.subTest(criteria=criteria):
                result, rows = self.judge_cached(
                    {"n_passed": 0, "n_criteria": criteria, "all_pass": False}
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIsNone(rows[0]["score"])
                self.assertIn("0 graded", result.stdout)

    def test_positive_criteria_preserve_zero_partial_and_full_scores(self):
        for passed in (0, 1, 2):
            with self.subTest(passed=passed):
                payload = {"n_passed": passed, "n_criteria": 2, "all_pass": passed == 2}
                result, rows = self.judge_cached(payload)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(rows[0]["score"], payload)
                self.assertIn("1 graded", result.stdout)

    def test_selected_run_uses_the_same_criteria_contract(self):
        for criteria, status in ((None, 2), (0, 2), (2, 0)):
            with self.subTest(criteria=criteria):
                result, rows = self.judge_cached(
                    {"n_passed": 0, "n_criteria": criteria, "all_pass": False}, bases=False
                )
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertEqual(rows[0]["graded"], criteria == 2)

    def test_absent_grade_fields_do_not_count_as_a_completed_grade(self):
        result, rows = self.judge_cached({})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIsNone(rows[0]["score"])

    def test_missing_numerator_is_ungraded_for_baseline_and_selected_runs(self):
        for bases in (False, True):
            with self.subTest(bases=bases):
                result, rows = self.judge_cached({"n_criteria": 2, "all_pass": False}, bases=bases)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                if bases:
                    self.assertIsNone(rows[0]["score"])
                else:
                    self.assertFalse(rows[0]["graded"])
                    self.assertIsNone(rows[0]["frac"])


if __name__ == "__main__":
    unittest.main()
