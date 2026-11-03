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

"""Tests for benchmark task partition validation."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.tasks import select


class TaskSplitTests(unittest.TestCase):
    def write_split(self, data: dict) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "split.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_rejects_split_that_omits_dataset_tasks(self):
        path = self.write_split({"benchmark": "lab", "train": ["task-a"], "test": []})

        with self.assertRaisesRegex(ValueError, r"omits task IDs.*task-b"):
            select({"task-a": "domain", "task-b": "domain"}, "all", path, benchmark="lab")

    def test_accepts_complete_partition_for_each_selection(self):
        path = self.write_split(
            {"benchmark": "lab", "train": ["task-b"], "test": ["task-a"]}
        )
        tasks = {"task-a": "domain", "task-b": "domain"}

        self.assertEqual(select(tasks, "all", path, benchmark="lab"), ["task-a", "task-b"])
        self.assertEqual(select(tasks, "train", path, benchmark="lab"), ["task-b"])
        self.assertEqual(select(tasks, "test", path, benchmark="lab"), ["task-a"])


if __name__ == "__main__":
    unittest.main()
