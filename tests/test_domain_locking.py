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

"""Cross-process ownership of mutable per-domain experience streams."""

import fcntl
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core import schedule
from harness2.core.library import Library, lock_streams
from harness2.demo import DemoAdapter, DemoProposer


class DomainLockingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.processes = multiprocessing.get_context("fork")

    def join_workers(self, children):
        for child in children:
            child.join(20)
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
        self.assertEqual([child.exitcode for child in children], [0] * len(children))

    def test_concurrent_resume_preserves_the_active_streams_prior_lessons(self):
        at_second_task = self.processes.Event()
        release = self.processes.Event()
        events, results = self.processes.Queue(), self.processes.Queue()
        adapter = self.adapter
        execute = adapter.execute

        def worker(first):
            proposer = DemoProposer(adapter.spec.channels["proposer"])
            if first:

                def pause_before_second_task(task_id, harness_dir, run_id, **kwargs):
                    if task_id == "ledger-2" and run_id.endswith("/base1"):
                        at_second_task.set()
                        if not release.wait(20):
                            raise RuntimeError("test did not release first stream")
                    return execute(task_id, harness_dir, run_id, **kwargs)

                adapter.execute = pause_before_second_task

            original_flock, original_retain = fcntl.flock, Library.retain

            def flock(file, operation):
                try:
                    return original_flock(file, operation)
                except BlockingIOError:
                    events.put("blocked")
                    raise

            def retain(library, task_ids):
                original_retain(library, task_ids)
                events.put("retained")

            with (
                patch("fcntl.flock", original_flock if first else flock),
                patch.object(Library, "retain", original_retain if first else retain),
            ):
                result = schedule.run_domain(
                    "accounting",
                    adapter.tasks(),
                    1,
                    adapter=adapter,
                    prop=proposer,
                    runs_root=adapter.spec.runs_root,
                    library_root=self.root / "library",
                    modes=("agg",),
                )
            results.put((first, result, adapter.execution_count))

        first = self.processes.Process(target=worker, args=(True,))
        second = self.processes.Process(target=worker, args=(False,))
        children = [first]
        first.start()
        try:
            self.assertTrue(at_second_task.wait(20))
            second.start()
            children.append(second)
            # An actual failed nonblocking flock establishes contention. Without
            # ownership, the second process reaches retain([]) and deletes the
            # lesson the paused first process is about to read.
            self.assertEqual(events.get(timeout=20), "blocked")
            prior = self.root / "library/agg/accounting/harnesses/ledger-1/EXPERIENCE.md"
            self.assertTrue(prior.is_file())
        finally:
            release.set()
            self.join_workers(children)

        rows = {
            first: (result, count)
            for first, result, count in (results.get(timeout=5) for _ in children)
        }
        self.assertEqual((rows[True][0]["done"], rows[True][0]["failed"]), (2, 0))
        self.assertEqual((rows[False][0]["done"], rows[False][0]["failed"]), (2, 0))
        self.assertEqual([row["status"] for row in rows[False][0]["tasks"]], ["skipped"] * 2)
        self.assertEqual(rows[False][1], 0)
        context = self.root / "runs/demo-oc-agg1/ledger-2/context/inv1/library"
        self.assertTrue((context / "harnesses/ledger-1/EXPERIENCE.md").is_file())
        self.assertFalse(list(context.rglob("*.stream.lock")))

    def test_other_domains_and_modes_can_acquire_ownership(self):
        surface = self.adapter.substrate.surface()
        root = self.root / "library"
        held = Library(root / "agg", "accounting", surface)
        others = [
            Library(root / "agg", "legal", surface),
            Library(root / "seq", "accounting", surface),
        ]
        acquired = self.processes.Queue()

        def worker():
            with lock_streams(others):
                acquired.put(True)

        with lock_streams([held]):
            child = self.processes.Process(target=worker)
            child.start()
            try:
                self.assertTrue(acquired.get(timeout=10))
            finally:
                self.join_workers([child])

    def test_worker_exit_releases_ownership(self):
        library = Library(self.root / "library/agg", "accounting", self.adapter.substrate.surface())
        ready = self.processes.Event()

        def worker():
            with lock_streams([library]):
                ready.set()
                os._exit(0)

        child = self.processes.Process(target=worker)
        child.start()
        try:
            self.assertTrue(ready.wait(10))
        finally:
            self.join_workers([child])
        # Fail immediately if the dead process somehow retained ownership.
        path = library.dir.with_name(f".{library.dir.name}.stream.lock")
        with path.open("a") as file:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with lock_streams([library]):
            pass


if __name__ == "__main__":
    unittest.main()
