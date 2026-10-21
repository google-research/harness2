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

"""Unit tests for BlueprintAdapter in harness2.benchmarks.blueprint."""

import shutil
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.blueprint import BlueprintAdapter
from harness2.core.spec import BenchmarkAdapter


class TestBlueprintAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.adapter = BlueprintAdapter(self.root)

    def test_instantiation_and_spec_protocol(self):
        adapter = BlueprintAdapter(Path("runs/test_bp"))
        self.assertEqual(adapter.spec.name, "bp")
        self.assertIsInstance(adapter, BenchmarkAdapter)
        if Path("runs/test_bp").exists():
            shutil.rmtree(Path("runs/test_bp"), ignore_errors=True)

    def test_tasks_list(self):
        expected_tasks = [
            "wi-authoring-standard",
            "doc-integrity-fix",
            "plantuml-sync",
        ]
        self.assertEqual(self.adapter.tasks(), expected_tasks)
        self.assertEqual(self.adapter.tasks(split="all"), expected_tasks)

    def test_domains(self):
        self.assertEqual(self.adapter.domain_of("wi-authoring-standard"), "wi")
        self.assertEqual(self.adapter.domain_of("doc-integrity-fix"), "integrity")
        self.assertEqual(self.adapter.domain_of("plantuml-sync"), "diagrams")

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

        # Test fallback harness when blueprint_root has no .harness
        empty_root = self.root / "empty_blueprint"
        empty_root.mkdir(parents=True, exist_ok=True)
        fallback_adapter = BlueprintAdapter(self.root, blueprint_root=empty_root)
        fallback_base = fallback_adapter.base_harness()
        self.assertIsInstance(fallback_base, Path)
        self.assertTrue(fallback_base.is_dir())
        self.assertTrue((fallback_base / "systemprompt.md").exists())

    def test_execute_creates_run_dir_with_trajectory_and_deliverable(self):
        run_id = "test-run-wi"
        run_dir = self.adapter.execute("wi-authoring-standard", None, run_id)
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
        self.assertIn("eob_wi_and_350_sample_wi.md", files)
        deliv_text = self.adapter.deliverable_text(run_dir)
        self.assertIn("Sample Operational Instruction", deliv_text)

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
        # 1. Test full passing solution on wi-authoring-standard
        run_wi = self.root / "executions" / "perfect-wi"
        run_wi.mkdir(parents=True, exist_ok=True)
        (run_wi / "eob_wi_and_350_sample_wi.md").write_text(
            "# Work Instruction: Standard System Operations\n\n"
            "Document Code: EOB-WI-AND-350\n"
            "Version: 1.0\n"
            "Effective Date: 2026-10-21\n\n"
            "## 1. Governance RACI Matrix\n\n"
            "| Activity | A&D Manager | Domain Leader | Project/Product Owner | Project/Product Member |\n"
            "|---|---|---|---|---|\n"
            "| Architecture Sign-off | A | R | C | I |\n"
            "| Implementation | I | A | R | R |\n\n"
            "## 2. Revision History\n"
            "| Version | Date | Author | Description |\n"
            "|---|---|---|---|\n"
            "| 1.0 | 2026-10-21 | A&D Team | Migrated from Module 239 revision history |\n",
            encoding="utf-8",
        )
        score_wi = self.adapter.score(run_wi, "wi-authoring-standard")
        self.assertIsNotNone(score_wi)
        self.assertEqual(score_wi["n_passed"], score_wi["n_criteria"])
        self.assertTrue(score_wi["all_pass"])

        # 2. Test full passing solution on doc-integrity-fix
        run_integrity = self.root / "executions" / "perfect-integrity"
        run_integrity.mkdir(parents=True, exist_ok=True)
        (run_integrity / "eob_roadmap_and_002_sample.md").write_text(
            "# Strategic Roadmap Execution Sample\n\n"
            "Document Code: EOB-ROADMAP-AND-002\n"
            "Version: 1.0.0\n\n"
            "## Referenced Documents\n"
            "- Standard Procedure: [Canonical WI](03-work-instructions/eob_wi_and_020_procedure.md)\n"
            "- Associated Standard: EOB-WI-AND-020\n\n"
            "## Description\n"
            "This roadmap aligns with EOB-WI-AND-020 governance standard.\n",
            encoding="utf-8",
        )
        score_integrity = self.adapter.score(run_integrity, "doc-integrity-fix")
        self.assertIsNotNone(score_integrity)
        self.assertEqual(score_integrity["n_passed"], score_integrity["n_criteria"])
        self.assertTrue(score_integrity["all_pass"])

        # 3. Test full passing solution on plantuml-sync
        run_puml = self.root / "executions" / "perfect-puml"
        run_puml.mkdir(parents=True, exist_ok=True)
        (run_puml / "sample_workflow.puml").write_text(
            "@startuml\n"
            "!theme plain\n"
            "scale max 1050 width\n"
            "top to bottom direction\n\n"
            "rectangle \"Ingestion\" as Ingest\n"
            "rectangle \"Processing\" as Process\n"
            "rectangle \"Archive\" as Archive\n\n"
            "Ingest --> Process\n"
            "Process --> Archive\n"
            "@enduml\n",
            encoding="utf-8",
        )
        score_puml = self.adapter.score(run_puml, "plantuml-sync")
        self.assertIsNotNone(score_puml)
        self.assertEqual(score_puml["n_passed"], score_puml["n_criteria"])
        self.assertTrue(score_puml["all_pass"])

    def test_score_raci_multiple_a_fails(self):
        run_dir = self.root / "executions" / "invalid-raci"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "eob_wi_and_350_sample_wi.md").write_text(
            "# Work Instruction\n"
            "Document Code: EOB-WI-AND-350\n"
            "Version: 1.0\n\n"
            "| Activity | A&D Manager | Domain Leader | Project/Product Owner | Project/Product Member |\n"
            "|---|---|---|---|---|\n"
            "| Invalid Row | A | A | C | I |\n\n"
            "Revision History: Module 239\n",
            encoding="utf-8",
        )
        score_res = self.adapter.score(run_dir, "wi-authoring-standard")
        self.assertIsNotNone(score_res)
        self.assertFalse(score_res["criteria"]["raci_single_a"])
        self.assertFalse(score_res["all_pass"])


if __name__ == "__main__":
    unittest.main()
