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

"""Regression test for JobBench staged store refresh when task fingerprint changes (Issue #198)."""

import errno
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.core import runmark as RM


class JobBenchStoreRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = self.root / "store"
        self.dataset = self.root / "dataset"
        self.task_id = "finance/budget_audit"

        self.task_src = self.dataset / self.task_id / "task_folder"
        self.task_src.mkdir(parents=True)
        self.instruction_file = self.task_src / "TASK_INSTRUCTIONS.txt"
        self.instruction_file.write_text("Instruction V1: Count rows", encoding="utf-8")

        self.data_file = self.task_src / "data.csv"
        self.data_file.write_text("a,b\n1,2\n", encoding="utf-8")

    def _make_adapter(self):
        with patch.object(JobBenchAdapter, "__init__", lambda s, **kw: None):
            adapter = JobBenchAdapter()
            adapter.store = self.store
            adapter._dataset_task = lambda tid: self.dataset / tid
            adapter.task_prompt = lambda tid: (
                self.dataset / tid / "task_folder" / "TASK_INSTRUCTIONS.txt"
            ).read_text(encoding="utf-8")
            adapter.task_files = lambda tid: self.dataset / tid / "task_folder"
            return adapter

    def test_staged_store_refreshes_when_task_instructions_change(self):
        adapter = self._make_adapter()

        # Step 1: Initial staging
        staged = adapter._ensure_store_task(self.task_id)
        self.assertEqual(
            (staged / "TASK_INSTRUCTIONS.txt").read_text(encoding="utf-8"),
            "Instruction V1: Count rows",
        )

        # Step 2: Unchanged inputs return existing cached store dir
        staged_repeat = adapter._ensure_store_task(self.task_id)
        self.assertEqual(
            (staged_repeat / "TASK_INSTRUCTIONS.txt").read_text(encoding="utf-8"),
            "Instruction V1: Count rows",
        )

        # Step 3: Change instructions (fingerprint changes)
        self.instruction_file.write_text("Instruction V2: Sum columns", encoding="utf-8")

        # Step 4: Staged store must refresh with updated instructions
        staged_updated = adapter._ensure_store_task(self.task_id)
        self.assertEqual(
            (staged_updated / "TASK_INSTRUCTIONS.txt").read_text(encoding="utf-8"),
            "Instruction V2: Sum columns",
        )

    def test_staged_store_refreshes_when_task_source_files_change(self):
        adapter = self._make_adapter()

        # Step 1: Initial staging
        staged = adapter._ensure_store_task(self.task_id)
        self.assertEqual(
            (staged / "data.csv").read_text(encoding="utf-8"),
            "a,b\n1,2\n",
        )

        # Step 2: Modify data file
        self.data_file.write_text("a,b\n10,20\n30,40\n", encoding="utf-8")

        # Step 3: Staged store must refresh with updated data file
        staged_updated = adapter._ensure_store_task(self.task_id)
        self.assertEqual(
            (staged_updated / "data.csv").read_text(encoding="utf-8"),
            "a,b\n10,20\n30,40\n",
        )


class RefreshFailureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = JobBenchStoreRefreshTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.adapter = self.fixture._make_adapter()

    def test_failed_publish_retains_previous_complete_task(self):
        staged = self.adapter._ensure_store_task(self.fixture.task_id)
        previous = (staged / "TASK_INSTRUCTIONS.txt").read_bytes()
        self.fixture.instruction_file.write_text("Instruction V2", encoding="utf-8")
        rename = os.rename

        def fail_publish(src, dst):
            if Path(dst) == staged:
                raise OSError(errno.EIO, "injected publication failure")
            return rename(src, dst)

        with patch("harness2.benchmarks.jobbench.os.rename", side_effect=fail_publish):
            with self.assertRaises(OSError):
                self.adapter._ensure_store_task(self.fixture.task_id)

        self.assertTrue(staged.is_dir(), "a failed refresh removed the last complete task")
        self.assertEqual((staged / "TASK_INSTRUCTIONS.txt").read_bytes(), previous)

    def test_retry_after_partial_copy_does_not_publish_deleted_source_file(self):
        self.adapter._ensure_store_task(self.fixture.task_id)
        self.fixture.instruction_file.write_text("Instruction V2", encoding="utf-8")
        removed = self.fixture.task_src / "removed-between-attempts.txt"
        removed.write_text("obsolete input", encoding="utf-8")

        def partial_copy(src, dst, **kwargs):
            Path(dst).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(removed, Path(dst) / removed.name)
            raise OSError(errno.ENOSPC, "injected partial-copy failure")

        with patch("harness2.benchmarks.jobbench.shutil.copytree", side_effect=partial_copy):
            with self.assertRaises(OSError):
                self.adapter._ensure_store_task(self.fixture.task_id)

        removed.unlink()
        staged = self.adapter._ensure_store_task(self.fixture.task_id)
        self.assertFalse(
            (staged / removed.name).exists(),
            "the successful retry published an input absent from the fingerprinted dataset",
        )


class FingerprintPublishFailureTests(unittest.TestCase):
    def test_failed_fingerprint_write_retains_previous_complete_task(self):
        fixture = JobBenchStoreRefreshTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        adapter = fixture._make_adapter()
        staged = adapter._ensure_store_task(fixture.task_id)
        previous = (staged / "TASK_INSTRUCTIONS.txt").read_bytes()
        fixture.instruction_file.write_text("Instruction V2", encoding="utf-8")
        fp = adapter.store / fixture.task_id / ".inputs_fingerprint"
        write = Path.write_text

        def fail_stamp(path, *args, **kwargs):
            if path == fp:
                raise OSError(errno.EIO, "injected fingerprint publication failure")
            return write(path, *args, **kwargs)

        with patch.object(Path, "write_text", new=fail_stamp):
            with self.assertRaises(OSError):
                adapter._ensure_store_task(fixture.task_id)

        self.assertTrue(staged.is_dir())
        self.assertEqual((staged / "TASK_INSTRUCTIONS.txt").read_bytes(), previous)

    def test_failed_rollback_does_not_delete_the_complete_backup(self):
        fixture = JobBenchStoreRefreshTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        adapter = fixture._make_adapter()
        staged = adapter._ensure_store_task(fixture.task_id)
        previous = (staged / "TASK_INSTRUCTIONS.txt").read_bytes()
        fixture.instruction_file.write_text("Instruction V2", encoding="utf-8")
        fp = adapter.store / fixture.task_id / ".inputs_fingerprint"
        write = Path.write_text
        replace = os.replace

        def fail_stamp(path, *args, **kwargs):
            if path == fp:
                raise OSError(errno.EIO, "injected fingerprint publication failure")
            return write(path, *args, **kwargs)

        def fail_restore(src, dst):
            if Path(src).name.startswith(".task_folder.old."):
                raise OSError(errno.EIO, "injected rollback failure")
            return replace(src, dst)

        with patch.object(Path, "write_text", new=fail_stamp), patch(
            "harness2.benchmarks.jobbench.os.replace", side_effect=fail_restore
        ):
            with self.assertRaises(OSError):
                adapter._ensure_store_task(fixture.task_id)

        backups = list(staged.parent.glob(".task_folder.old.*"))
        self.assertEqual(len(backups), 1, "failed rollback deleted the only previous complete task")
        self.assertEqual((backups[0] / "TASK_INSTRUCTIONS.txt").read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()

