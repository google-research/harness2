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

"""Unit tests for DamsAdapter in harness2.benchmarks.dams."""

import shutil
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.dams import DamsAdapter
from harness2.core.spec import BenchmarkAdapter


class TestDamsAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.adapter = DamsAdapter(self.root)

    def test_instantiation_and_spec_protocol(self):
        adapter = DamsAdapter(Path("runs/test_dams"))
        self.assertEqual(adapter.spec.name, "dams")
        self.assertIsInstance(adapter, BenchmarkAdapter)
        # Clean up Path("runs/test_dams") if created
        if Path("runs/test_dams").exists():
            shutil.rmtree(Path("runs/test_dams"), ignore_errors=True)

    def test_tasks_list(self):
        expected_tasks = [
            "backend-kpi-endpoint",
            "frontend-kpi-card",
            "deptrac-boundary-contract",
        ]
        self.assertEqual(self.adapter.tasks(), expected_tasks)
        self.assertEqual(self.adapter.tasks(split="all"), expected_tasks)

    def test_domains(self):
        self.assertEqual(self.adapter.domain_of("backend-kpi-endpoint"), "backend")
        self.assertEqual(self.adapter.domain_of("frontend-kpi-card"), "frontend")
        self.assertEqual(self.adapter.domain_of("deptrac-boundary-contract"), "architecture")

    def test_task_prompts(self):
        for task_id in self.adapter.tasks():
            prompt = self.adapter.task_prompt(task_id)
            self.assertIsInstance(prompt, str)
            self.assertTrue(len(prompt.strip()) > 0, f"Empty prompt for task: {task_id}")

    def test_task_files(self):
        for task_id in self.adapter.tasks():
            task_dir = self.adapter.task_files(task_id)
            self.assertIsNotNone(task_dir)
            self.assertIsInstance(task_dir, Path)
            self.assertTrue(task_dir.is_dir(), f"Task directory missing: {task_dir}")

    def test_base_harness(self):
        base = self.adapter.base_harness()
        self.assertIsInstance(base, Path)
        self.assertTrue(base.exists(), f"Base harness does not exist: {base}")

        # Test fallback harness when dams_root has no .harness
        empty_dams_root = self.root / "empty_dams"
        empty_dams_root.mkdir(parents=True, exist_ok=True)
        fallback_adapter = DamsAdapter(self.root, dams_root=empty_dams_root)
        fallback_base = fallback_adapter.base_harness()
        self.assertIsInstance(fallback_base, Path)
        self.assertTrue(fallback_base.is_dir())

    def test_execute_creates_run_dir_with_trajectory_and_deliverable(self):
        run_id = "test-run-backend"
        run_dir = self.adapter.execute("backend-kpi-endpoint", None, run_id)
        self.assertIsInstance(run_dir, Path)
        self.assertTrue(run_dir.is_dir())

        # Check trajectory.md
        trajectory_file = run_dir / "trajectory.md"
        self.assertTrue(trajectory_file.is_file(), "trajectory.md missing from run_dir")
        self.assertTrue(len(trajectory_file.read_text(encoding="utf-8").strip()) > 0)

        # Check delivered() returns True
        self.assertTrue(self.adapter.delivered(run_dir))

        # Check trajectory() method
        traj_text = self.adapter.trajectory(run_dir, obs_trunc=50000)
        self.assertIn("[ACTION]", traj_text)

        # Check deliverable_files and deliverable_text
        files = self.adapter.deliverable_files(run_dir)
        self.assertIn("ParameterKpiController.php", files)
        deliv_text = self.adapter.deliverable_text(run_dir)
        self.assertIn("ParameterKpiController", deliv_text)

    def test_score_evaluates_run_dir(self):
        for task_id in self.adapter.tasks():
            run_id = f"test-run-{task_id}"
            run_dir = self.adapter.execute(task_id, None, run_id)
            score_res = self.adapter.score(run_dir, task_id)
            self.assertIsInstance(score_res, dict)
            self.assertIn("n_passed", score_res)
            self.assertIn("n_criteria", score_res)
            self.assertIsInstance(score_res["n_passed"], int)
            self.assertIsInstance(score_res["n_criteria"], int)
            self.assertGreater(score_res["n_criteria"], 0)
            self.assertGreaterEqual(score_res["n_passed"], 0)
            self.assertLessEqual(score_res["n_passed"], score_res["n_criteria"])

    def test_score_full_pass(self):
        # Test full passing solution on backend-kpi-endpoint
        run_dir = self.root / "executions" / "perfect-backend"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "ParameterKpiController.php").write_text(
            "<?php\nnamespace Modules\\Monitoring\\Http\\Controllers;\n"
            "class ParameterKpiController {\n"
            "    public function calculate($request) {\n"
            "        return response()->json(['status' => 'success', 'value' => 95.0, 'deviation' => -5.0]);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        (run_dir / "ParameterKpiRequest.php").write_text(
            "<?php\nnamespace Modules\\Monitoring\\Http\\Requests;\n"
            "class ParameterKpiRequest {}\n",
            encoding="utf-8",
        )
        score_res = self.adapter.score(run_dir, "backend-kpi-endpoint")
        self.assertIsNotNone(score_res)
        self.assertEqual(score_res["n_passed"], score_res["n_criteria"])
        self.assertTrue(score_res["all_pass"])


if __name__ == "__main__":
    unittest.main()
