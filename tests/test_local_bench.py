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

"""Unit and regression tests for the Local Benchmark Adapter (--bench local)."""

import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from harness2.benchmarks.local import (
    LocalAdapter,
    compute_inputs_fingerprint,
    discover_tasks,
)
from harness2.core import tree as HT
from harness2.core.spec import BenchmarkAdapter


class LocalBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tasks_dir = self.root / "tasks"
        self.tasks_dir.mkdir()

        # Create two domains with tasks
        self.t1 = self.tasks_dir / "accounting" / "reconcile"
        self.t1.mkdir(parents=True)
        (self.t1 / "task.md").write_text("Sum the ledger and write total.txt", encoding="utf-8")
        (self.t1 / "task.toml").write_text(
            'outputs = ["total.txt"]\ncheck = ["python", "check.py"]\ntimeout = 60\n',
            encoding="utf-8",
        )
        t1_files = self.t1 / "files"
        t1_files.mkdir()
        (t1_files / "ledger.csv").write_text("amount\n10\n20\n", encoding="utf-8")

        self.t2 = self.tasks_dir / "dev" / "fix_bug"
        self.t2.mkdir(parents=True)
        (self.t2 / "task.md").write_text("Fix index error in app.py", encoding="utf-8")

    def test_protocol_conformance(self):
        adapter = LocalAdapter(self.tasks_dir, substrate="oc")
        self.assertTrue(isinstance(adapter, BenchmarkAdapter))
        self.assertTrue(adapter.spec.name.startswith("local-"))
        self.assertEqual(adapter.spec.domain_word, "domain")

    def test_task_discovery_and_domain_mapping(self):
        adapter = LocalAdapter(self.tasks_dir, substrate="oc")
        tasks = adapter.tasks()
        self.assertEqual(tasks, ["accounting/reconcile", "dev/fix_bug"])
        self.assertEqual(adapter.domain_of("accounting/reconcile"), "accounting")
        self.assertEqual(adapter.domain_of("dev/fix_bug"), "dev")
        self.assertIn("Sum the ledger", adapter.task_prompt("accounting/reconcile"))
        self.assertTrue(adapter.task_files("accounting/reconcile").is_dir())
        self.assertIsNone(adapter.task_files("dev/fix_bug"))

    def test_task_metadata_parsing(self):
        adapter = LocalAdapter(self.tasks_dir, substrate="oc")
        m1 = adapter.task_meta("accounting/reconcile")
        self.assertEqual(m1.outputs, ("total.txt",))
        self.assertEqual(m1.check, ("python", "check.py"))
        self.assertEqual(m1.timeout, 60)

        # Task without task.toml has clean defaults
        m2 = adapter.task_meta("dev/fix_bug")
        self.assertEqual(m2.outputs, ())
        self.assertEqual(m2.check, ())
        self.assertEqual(m2.timeout, 3600)

    def test_input_fingerprinting_and_change_detection(self):
        adapter = LocalAdapter(self.tasks_dir, substrate="oc")
        fp1 = adapter.task_fingerprint("accounting/reconcile")
        self.assertEqual(len(fp1), 16)

        # Same inputs produce identical digest
        fp2 = adapter.task_fingerprint("accounting/reconcile")
        self.assertEqual(fp1, fp2)

        # Modifying prompt changes digest
        (self.t1 / "task.md").write_text("Modified prompt instructions", encoding="utf-8")
        fp_mod_prompt = adapter.task_fingerprint("accounting/reconcile")
        self.assertNotEqual(fp1, fp_mod_prompt)

        # Modifying files changes digest
        (self.t1 / "task.md").write_text("Sum the ledger and write total.txt", encoding="utf-8")
        (self.t1 / "files" / "ledger.csv").write_text("amount\n999\n", encoding="utf-8")
        fp_mod_files = adapter.task_fingerprint("accounting/reconcile")
        self.assertNotEqual(fp1, fp_mod_files)

    def test_base_harness_generation(self):
        for sub in ("oc", "cx"):
            with self.subTest(substrate=sub):
                adapter = LocalAdapter(self.tasks_dir, substrate=sub)
                base = adapter.base_harness()
                self.assertTrue(base.is_dir())
                self.assertTrue(HT.is_tree(adapter.substrate.surface(), base))
                # Validate tree passes for base
                tree_dict = HT.read_tree(adapter.substrate.surface(), base)
                adapter.validate_tree(tree_dict)

    def test_execution_workspace_isolation_and_delivery(self):
        adapter = LocalAdapter(self.tasks_dir, substrate="oc")
        # Create a mock runner script to simulate agent execution
        mock_runner = self.root / "mock_agent.py"
        mock_runner.write_text(
            "import sys, pathlib\n"
            "ws = pathlib.Path(sys.argv[1])\n"
            "(ws / 'total.txt').write_text('30')\n"
            "with open(ws / 'ledger.csv', 'a') as f: f.write('MODIFIED\\n')\n",
            encoding="utf-8",
        )

        with unittest.mock.patch.dict(os.environ, {"HARNESS2_LOCAL_MOCK_RUNNER": str(mock_runner)}):
            run_dir = adapter.execute("accounting/reconcile", None, "run-001")
            self.assertTrue(run_dir.is_dir())
            self.assertTrue(adapter.delivered(run_dir))
            files = adapter.deliverable_files(run_dir)
            self.assertIn("total.txt", files)
            self.assertEqual(files["total.txt"], "30")

            # Crucial test: original files/ in task directory were NOT modified
            orig_ledger = (self.t1 / "files" / "ledger.csv").read_text(encoding="utf-8")
            self.assertNotIn("MODIFIED", orig_ledger)

    def test_score_not_configured_vs_check_command(self):
        adapter = LocalAdapter(self.tasks_dir, substrate="oc")
        run_dir = adapter._run_dir("run-test-score")
        ws = run_dir / "workspace"
        ws.mkdir(parents=True)

        # Task without check returns not_configured
        score_unconfigured = adapter.score(run_dir, "dev/fix_bug")
        self.assertEqual(score_unconfigured["status"], "not_configured")
        self.assertEqual(score_unconfigured["n_criteria"], 0)
        self.assertIsNone(score_unconfigured["n_passed"])

        # Task with check command:
        # Case A: Passing check
        (ws / "check.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
        score_pass = adapter.score(run_dir, "accounting/reconcile")
        self.assertEqual(score_pass["status"], "completed")
        self.assertEqual(score_pass["n_passed"], 1)
        self.assertEqual(score_pass["n_criteria"], 1)
        self.assertTrue(score_pass["all_pass"])

        # Case B: Failing check
        (ws / "check.py").write_text("import sys; sys.exit(1)\n", encoding="utf-8")
        score_fail = adapter.score(run_dir, "accounting/reconcile")
        self.assertEqual(score_fail["status"], "completed")
        self.assertEqual(score_fail["n_passed"], 0)
        self.assertEqual(score_fail["n_criteria"], 1)
        self.assertFalse(score_fail["all_pass"])


if __name__ == "__main__":
    unittest.main()
