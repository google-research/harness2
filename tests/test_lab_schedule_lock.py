# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Lab task exclusion is keyed by the shared result tree, not summaries."""

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks.lab import LabAdapter
from harness2.core import schedule
from harness2.demo import DemoAdapter, DemoProposer


class LabScheduledDemo(DemoAdapter):
    _run_dir = LabAdapter._run_dir
    schedule_task_lock = getattr(LabAdapter, "schedule_task_lock", None)


class LabScheduleLockTests(unittest.TestCase):
    def test_busy_result_namespace_is_rejected_before_any_second_rollout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            a = LabScheduledDemo(root / "alpha")
            b = LabScheduledDemo(root / "beta")
            a._results_root = b._results_root = root / "shared-results"
            mode_started = threading.Event()
            resume = threading.Event()
            original = schedule._run_one_mode

            def hold(*args, **kwargs):
                if kwargs["adapter"] is a:
                    mode_started.set()
                    if not resume.wait(timeout=10):
                        raise TimeoutError("test did not release the first run")
                return original(*args, **kwargs)

            def run(adapter, sweep, tag="same"):
                return schedule.run_task(
                    "ledger-1", 1, adapter=adapter, prop=DemoProposer(adapter.spec.channels["proposer"]),
                    runs_root=root / sweep, modes=("agg",), tag=tag,
                )

            with patch.object(schedule, "_run_one_mode", side_effect=hold), ThreadPoolExecutor() as pool:
                first = pool.submit(run, a, "sweep-a")
                try:
                    self.assertTrue(mode_started.wait(timeout=10))
                    with self.assertRaisesRegex(RuntimeError, "Lab result namespace is already in use"):
                        run(b, "sweep-b")
                    self.assertEqual(b.execution_count, 0)
                    self.assertFalse((root / "sweep-b").exists())
                    self.assertEqual(run(b, "sweep-b", tag="other").status, "ok")
                finally:
                    resume.set()
                self.assertEqual(first.result(timeout=10).status, "ok")
            self.assertEqual(run(b, "sweep-b").status, "ok")

    def test_error_releases_the_namespace_for_the_next_scheduler(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter = LabScheduledDemo(Path(temporary))
            adapter._results_root = Path(temporary) / "results"
            run_id = "lab-oc-agg1/ledger-1/base1"
            with self.assertRaisesRegex(ValueError, "aborted"):
                with adapter.schedule_task_lock(run_id):
                    raise ValueError("aborted")
            with adapter.schedule_task_lock(run_id):
                self.assertTrue((adapter._run_dir(run_id).parent / ".schedule.lock").is_file())
