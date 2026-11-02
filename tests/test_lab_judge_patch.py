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

"""Apply the shipped LAB patch to its exact MIT upstream and replay grading offline."""

import ast
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness2 import config
from harness2.benchmarks.lab import LabAdapter
from harness2.core.judge import judge_one

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/harvey-labs"
PATCH = ROOT / "third_party/patches/harvey-labs-judge-vertex-anthropic.diff"


def load_nodes(path, names, scope):
    """Execute exact upstream definitions, excluding dependency/client imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in names for target in node.targets
        ):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)


class LabJudgePatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        provenance = json.loads((FIXTURE / "PROVENANCE.json").read_text())
        self.assertEqual(provenance["commit"], config.DEPENDENCY_PINS["harvey-labs"])
        for name, expected in provenance["sha256"].items():
            self.assertEqual(hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest(), expected)
        assets = json.loads((ROOT / "dependencies/ASSETS.json").read_text())
        self.assertEqual(
            hashlib.sha256(PATCH.read_bytes()).hexdigest(),
            assets[PATCH.relative_to(ROOT).as_posix()],
        )
        upstream = self.root / "upstream"
        shutil.copytree(FIXTURE, upstream)
        for path in upstream.rglob("*.py.txt"):
            path.rename(path.with_suffix(""))
        subprocess.run(["git", "apply", str(PATCH)], cwd=upstream, check=True, capture_output=True)
        self.scope = {
            "__name__": __name__,
            "Path": Path,
            "json": json,
            "os": os,
            "re": re,
            "dataclass": dataclass,
            "field": field,
            "asdict": asdict,
            "StrEnum": StrEnum,
            "ThreadPoolExecutor": ThreadPoolExecutor,
            "subprocess": subprocess,
            "datetime": datetime,
            "timezone": timezone,
            "PROMPTS_DIR": upstream / "evaluation/prompts",
            "BENCH_ROOT": self.root,
            "RESULTS_DIR": self.root / "results",
        }
        load_nodes(upstream / "evaluation/judge.py", {"Judge"}, self.scope)
        load_nodes(
            upstream / "evaluation/scoring.py",
            {
                "DocxTrackChanges",
                "CriterionResult",
                "RubricResult",
                "_read_file_as_text",
                "_SKIP_DIRS",
                "_SKIP_EXTENSIONS",
                "_SKIP_FILES",
                "_load_all_output",
                "score_rubric",
            },
            self.scope,
        )
        load_nodes(
            upstream / "evaluation/run_eval.py",
            {
                "REQUIRED_TASK_KEYS",
                "REQUIRED_CRITERION_KEYS",
                "validate_task_config",
                "_resolve_task_dir",
                "evaluate_run",
            },
            self.scope,
        )
        task = self.root / "tasks/contracts/task-1"
        task.mkdir(parents=True)
        (task / "task.json").write_text(
            json.dumps(
                {
                    "title": "Review the local report",
                    "instructions": "Write report.txt",
                    "criteria": [
                        {
                            "id": "c1",
                            "title": "Correct report",
                            "match_criteria": "Review the report",
                        }
                    ],
                }
            )
        )
        self.run = self.root / "results/run-1"
        (self.run / "output").mkdir(parents=True)
        (self.run / "output/report.txt").write_text("The locally produced report.\n")

    def score(self, responses):
        transport = Mock(
            side_effect=[
                SimpleNamespace(
                    content=[SimpleNamespace(type="text", text=text)],
                    stop_reason="end_turn",
                )
                for text in responses
            ]
        )
        judge = object.__new__(self.scope["Judge"])
        judge.provider = "vertex-anthropic"
        judge.model = "fixture-judge"
        judge.client = SimpleNamespace(messages=SimpleNamespace(create=transport))
        with contextlib.redirect_stdout(io.StringIO()):
            scores = self.scope["evaluate_run"]("run-1", "contracts/task-1", judge, parallel=1)
        self.assertTrue((self.run / "scores.json").is_file())
        cached = judge_one(LabAdapter(), "contracts/task-1", "agg", 1, "agg1", "run-1", self.run)
        self.assertEqual(cached.all_pass, scores["all_pass"])
        return scores, cached, transport

    def test_conflicting_fenced_verdicts_retry_before_writing_a_score(self):
        response = 'Format:\n```json\n{"verdict":"pass","reasoning":"example"}\n```\nAnswer:\n```json\n{"verdict":"fail","reasoning":"actual verdict"}\n```'
        scores, cached, transport = self.score([response, '{"verdict":"fail","reasoning":"retry"}'])
        self.assertFalse(scores["all_pass"])
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(cached.frac, 0)

    def test_ambiguity_exhaustion_never_writes_a_score(self):
        response = '```json\n{"verdict":"pass"}\n```\n```json\n{"verdict":"fail"}\n```'
        with self.assertRaisesRegex(ValueError, "Ambiguous judge response"):
            self.score([response] * 4)
        self.assertFalse((self.run / "scores.json").exists())

    def test_agreeing_repeated_fences_are_a_valid_grade(self):
        response = '```json\n{"verdict":"pass","reasoning":"one"}\n```\n```json\n{"verdict":"pass","reasoning":"same"}\n```'
        scores, cached, transport = self.score([response])
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(scores["all_pass"])
        self.assertEqual(cached.frac, 1)

    def test_fail_verdict_controls(self):
        for response in (
            '{"verdict":"fail","reasoning":"not correct"}',
            'Review complete.\n```json\n{"verdict":"fail","reasoning":"not correct"}\n```',
        ):
            with self.subTest(response=response):
                scores, cached, transport = self.score([response])
                self.assertEqual(transport.call_count, 1)
                self.assertFalse(scores["all_pass"])
                self.assertEqual(cached.frac, 0)

    def test_existing_unfenced_ambiguity_guard_remains_active(self):
        response = '{"verdict":"pass"}\nAnswer: {"verdict":"fail"}'
        scores, cached, transport = self.score([response, '{"verdict":"fail","reasoning":"retry"}'])
        self.assertFalse(scores["all_pass"])
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(cached.frac, 0)


if __name__ == "__main__":
    unittest.main()
