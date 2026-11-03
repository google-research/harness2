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

"""Keep ambiguous WorkBuddy trial names from being assigned to a different task."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks import workbuddy


class TaskResolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "workbuddy"
        self.prefix = "customer_financial_report_" + "x" * 6
        self.assertEqual(len(self.prefix), 32)
        self.first = self.prefix + "alpha"
        self.second = self.prefix + "beta"
        self.unique = "independent_workflow_case_" + "y" * 20
        self.short = "standalone_unique_task"
        for task_id in (self.first, self.second, self.unique, self.short):
            self.add_task(task_id)
        repository = patch.object(workbuddy.config, "wb_repo", return_value=self.repo)
        repository.start()
        self.addCleanup(repository.stop)
        self.clear_caches()
        self.addCleanup(self.clear_caches)

    @staticmethod
    def clear_caches():
        workbuddy._exact_index.cache_clear()
        workbuddy.resolve_task.cache_clear()

    def add_task(self, task_id):
        directory = self.repo / "datasets/wb-bench-office-v1.0/tasks" / task_id
        directory.mkdir(parents=True)
        (directory / "task.toml").write_text('[metadata]\ncategory = "finance"\n', encoding="utf-8")
        (directory / "instruction.md").write_text("Write the requested report.\n", encoding="utf-8")

    def test_full_task_ids_and_unknown_names(self):
        for task_id in (self.first, self.second, self.unique, self.short):
            with self.subTest(task_id=task_id):
                self.assertEqual(workbuddy.resolve_task(task_id, "office"), task_id)
        self.assertIsNone(workbuddy.resolve_task("missing_task", "office"))

    def test_unique_truncated_name_is_resolved(self):
        self.assertEqual(workbuddy.resolve_task(self.unique[:32], "office"), self.unique)

    def test_ambiguous_truncated_name_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "ambiguous across 2 tasks"):
            workbuddy.resolve_task(self.prefix, "office")

    def test_other_ambiguous_prefixes_stay_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "ambiguous across 2 tasks"):
            workbuddy.resolve_task(self.prefix[:16], "office")

    def test_full_id_takes_priority_over_prefix_matches(self):
        self.add_task(self.prefix)
        self.clear_caches()
        self.assertEqual(workbuddy.resolve_task(self.prefix, "office"), self.prefix)

    def test_trial_discovery_rejects_ambiguous_truncated_name(self):
        run = self.root / "run"
        (run / (self.prefix + "__Ab123")).mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, "ambiguous across 2 tasks"):
            workbuddy._trials_in([run], self.first, "office")

    def test_trial_discovery_keeps_unique_and_full_names(self):
        run = self.root / "run"
        truncated = run / (self.unique[:32] + "__Ab123")
        complete = run / (self.second + "__Cd456")
        for directory in (truncated, complete, run / "unrelated_directory"):
            directory.mkdir(parents=True)
        self.assertEqual(workbuddy._trials_in([run], self.unique, "office"), [truncated])
        self.assertEqual(workbuddy._trials_in([run], self.second, "office"), [complete])
        self.assertEqual(workbuddy._trials_in([run], self.short, "office"), [])


if __name__ == "__main__":
    unittest.main()
