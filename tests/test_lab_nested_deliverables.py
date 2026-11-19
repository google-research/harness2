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

"""Replay exact pinned LAB nested artifact loading through grading and cache consumers."""

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


class LabNestedDeliverableTests(unittest.TestCase):
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
                "_is_thread_export",
                "_fuzzy_match_filename",
                "_match_deliverables",
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

    def score(self, actual_files, expected_files):
        (self.run / "output/report.txt").unlink()
        for name, body in actual_files.items():
            path = self.run / "output" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        task = self.root / "tasks/contracts/task-1/task.json"
        config = json.loads(task.read_text())
        if expected_files:
            config["criteria"][0]["deliverables"] = expected_files
        task.write_text(json.dumps(config))
        adapter = LabAdapter()
        self.assertTrue(adapter.delivered(self.run))

        def response(**kwargs):
            # A deterministic transport oracle checks whether actual artifact
            # bytes reached the judge, not the quality of professional work.
            prompt = kwargs["messages"][0]["content"]
            present = all(body in prompt for body in actual_files.values())
            text = json.dumps(
                {"verdict": "pass" if present else "fail", "reasoning": "local input oracle"}
            )
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn"
            )

        transport = Mock(side_effect=response)
        judge = object.__new__(self.scope["Judge"])
        judge.provider = "vertex-anthropic"
        judge.model = "fixture-judge"
        judge.client = SimpleNamespace(messages=SimpleNamespace(create=transport))
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.scope["evaluate_run"]("run-1", "contracts/task-1", judge, parallel=1)
        self.assertTrue((self.run / "scores.json").is_file())
        cached = judge_one(adapter, "contracts/task-1", "agg", 1, "agg1", "run-1", self.run)
        self.assertEqual(cached.all_pass, result["all_pass"])
        observed = transport.call_args.kwargs["messages"][0]["content"]
        for body in actual_files.values():
            self.assertIn(body, observed)
        self.assertEqual(cached.frac, 1)

    def test_basename_request_reads_nested_artifact(self):
        self.score({"reports/report.txt": "The actual nested report.\n"}, ["report.txt"])

    def test_declared_relative_path_keeps_its_identity(self):
        self.score(
            {"reports/report.txt": "The explicitly requested nested report.\n"},
            ["reports/report.txt"],
        )

    def test_distinct_nested_files_with_the_same_basename(self):
        self.score(
            {"current/report.txt": "Current report.\n", "archive/report.txt": "Archived report.\n"},
            ["current/report.txt", "archive/report.txt"],
        )

    def test_root_file_control(self):
        self.score({"report.txt": "The ordinary root report.\n"}, ["report.txt"])

    def test_no_deliverable_list_control(self):
        self.score({"reports/report.txt": "The recursive fallback report.\n"}, [])


if __name__ == "__main__":
    unittest.main()
