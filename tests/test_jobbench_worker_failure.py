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
"""Pinned JobBench report producers feed the real cache and grading consumers."""

import ast
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness2 import config
from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.cli.judge import _load_rows
from harness2.core import judge, report

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/job-bench-eval"


class JobbenchWorkerFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        provenance = json.loads((FIXTURE / "PROVENANCE.json").read_text())
        self.assertEqual(provenance["commit"], config.DEPENDENCY_PINS["job-bench-eval"])
        raw = (FIXTURE / provenance["file"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), provenance["sha256"])
        self.assertEqual(
            hashlib.sha256(
                (ROOT / "third_party/licenses/job-bench-eval.LICENSE").read_bytes()
            ).hexdigest(),
            provenance["license_sha256"],
        )
        source = self.root / "eval/judge.py"
        source.parent.mkdir()
        source.write_bytes(raw)
        patch = ROOT / "third_party/patches/job-bench-eval-judge.diff"
        assets = json.loads((ROOT / "dependencies/ASSETS.json").read_text())
        self.assertEqual(
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            assets[patch.relative_to(ROOT).as_posix()],
        )
        subprocess.run(["git", "apply", str(patch)], cwd=self.root, check=True, capture_output=True)
        tree = ast.parse(source.read_text())
        self.scope = {
            "Path": Path,
            "json": json,
            "os": os,
            "re": re,
            "time": time,
            "tempfile": tempfile,
            "datetime": datetime,
            "timezone": timezone,
        }
        names = {
            "normalize_criteria",
            "build_failed_rubric_result",
            "build_details_report",
            "write_json",
            "judge_rubric",
            "parse_judge_json",
        }
        nodes = [
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.scope)
        # Execute the actual pinned future-exception branch's producer call,
        # preserving its exact error marker rather than inventing report fields.
        self.worker_call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_failed_rubric_result"
            and len(node.args) > 2
            and isinstance(node.args[2], ast.JoinedStr)
            and any(
                isinstance(value, ast.Constant)
                and str(value.value).startswith("Judge raised an exception:")
                for value in node.args[2].values
            )
        )
        self.rubric = {
            "rubric": "Read the local output",
            "weight": 2,
            "criterion": ["A complete report"],
        }

    def worker_failure(self, index=0):
        return eval(
            compile(ast.Expression(self.worker_call), "pinned-worker-branch", "eval"),
            self.scope
            | {
                "idx": index,
                "rubrics": [self.rubric] * (index + 1),
                "exc": RuntimeError("offline worker failure"),
            },
        )

    def model_result(self, passed=None):
        content = json.dumps(
            {
                "rubric_passed": passed,
                "criteria_results": [{"passed": passed}],
                "overall_reasoning": "local response fixture",
            }
        )
        create = (
            Mock(side_effect=RuntimeError("offline transport failure"))
            if passed is None
            else Mock(
                return_value=SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
                )
            )
        )
        self.scope["get_openai_client"] = lambda *args: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        result, _debug = self.scope["judge_rubric"](
            0,
            self.rubric,
            "The local report.",
            "fixture-model",
            "local",
            "fixture-token",
            max_retries=1,
        )
        return result

    def consume(self, results):
        adapter = JobBenchAdapter(store_root=self.root / "store")
        run = self.root / "store/accounting/task1/model_output/agg1"
        run.mkdir(parents=True)
        (run / "report.txt").write_text("The actually produced local report.\n")
        self.assertTrue(adapter.delivered(run))
        _reporting, details = adapter._details_candidates("accounting/task1", "agg1")
        payload = self.scope["build_details_report"]("agg1", "fixture-judge", results)
        self.scope["write_json"](details, payload)
        # The model boundary remains offline; rejection should delegate to a
        # retry rather than accepting a failure report as a real zero.
        adapter._run_judge = Mock(return_value=False)
        scored = judge.judge_one(adapter, "accounting/task1", "agg", 1, "agg1", "run-1", run)
        cache = self.root / "judged.json"
        judge.write([scored], cache)
        loaded = _load_rows(cache)
        summary = report.summarise(loaded)[0]
        return scored, summary, details, adapter._run_judge

    def test_worker_exception_is_ungraded_and_quarantined(self):
        scored, summary, details, retry = self.consume([self.worker_failure()])
        self.assertFalse(scored.graded)
        self.assertIsNone(scored.frac)
        self.assertEqual((summary.n, summary.n_ungraded), (0, 1))
        self.assertTrue(details.with_name(details.name + ".judge_failed").is_file())
        retry.assert_called_once()

    def test_partial_worker_failure_does_not_lower_a_mixed_score(self):
        good = self.model_result(True)
        scored, summary, _details, retry = self.consume([good, self.worker_failure(1)])
        self.assertFalse(scored.graded)
        self.assertEqual((summary.n, summary.n_ungraded), (0, 1))
        retry.assert_called_once()

    def test_valid_zero_control_is_graded(self):
        scored, summary, details, retry = self.consume([self.model_result(False)])
        self.assertTrue(scored.graded)
        self.assertEqual(scored.frac, 0)
        self.assertEqual((summary.n, summary.n_ungraded), (1, 0))
        self.assertTrue(details.is_file())
        retry.assert_not_called()

    def test_valid_full_credit_control_is_graded(self):
        scored, summary, _details, retry = self.consume([self.model_result(True)])
        self.assertTrue(scored.graded)
        self.assertEqual(scored.frac, 1)
        self.assertEqual((summary.n, summary.n_ungraded), (1, 0))
        retry.assert_not_called()

    def test_existing_transport_failure_control_stays_ungraded(self):
        scored, summary, _details, retry = self.consume([self.model_result()])
        self.assertFalse(scored.graded)
        self.assertEqual((summary.n, summary.n_ungraded), (0, 1))
        retry.assert_called_once()


if __name__ == "__main__":
    unittest.main()
