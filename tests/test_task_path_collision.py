# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Distinct task IDs must never silently share results or experience."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.core import schedule
from harness2.demo import DemoAdapter, DemoProposer


class NamedDemo(DemoAdapter):
    def __init__(self, root, names, domains=None):
        super().__init__(root)
        self.names = names
        self.domains = domains or {}
        for name in names:
            source = self.task_files(name)
            source.mkdir(parents=True, exist_ok=True)
            (source / "ledger.csv").write_text("amount\n100.25\n-20.10\n5.00\n")

    def tasks(self, split="all"):
        return self.names

    def domain_of(self, task_id):
        return self.domains.get(task_id, "accounting")


class TaskPathCollisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.names = ["group/item", "group__item"]
        self.adapter = NamedDemo(self.root, self.names)
        self.proposer = DemoProposer(self.adapter.spec.channels["proposer"])
        self.options = dict(
            adapter=self.adapter,
            prop=self.proposer,
            runs_root=self.adapter.spec.runs_root,
            modes=("seq",),
        )

    def run_domain(self, names):
        return schedule.run_domain(
            "accounting", names, 1, library_root=self.root / "library", **self.options
        )

    def persisted(self):
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for directory in ("runs", "library")
            for p in (self.root / directory).rglob("*")
            if p.is_file()
        }

    def test_domain_rejects_colliding_ids_before_any_work(self):
        with self.assertRaisesRegex(ValueError, "group/item.*group__item|group__item.*group/item"):
            self.run_domain(self.names)
        self.assertEqual(self.adapter.execution_count, 0)
        self.assertFalse((self.root / "runs").exists())
        self.assertFalse((self.root / "library").exists())

    def test_sweep_rejects_collision_across_domains_before_dispatch(self):
        self.adapter.domains = dict(zip(self.names, ("first", "second")))
        with self.assertRaises(ValueError):
            schedule.run_sweep(
                1, library_root=self.root / "library", domain_parallel=2, **self.options
            )
        self.assertEqual(self.adapter.execution_count, 0)

    def test_stream_filters_can_select_one_nonconflicting_id(self):
        for name in self.names:
            self.assertEqual(
                schedule.task_stream(self.adapter, only_tasks=[name]), {"accounting": [name]}
            )
        self.assertEqual(sum(map(len, schedule.task_stream(self.adapter, max_tasks=1).values())), 1)

    def test_existing_summary_conflict_preserves_library_and_results(self):
        self.assertEqual(self.run_domain([self.names[0]])["tasks"][0]["status"], "ok")
        before, calls = self.persisted(), self.adapter.execution_count
        with self.assertRaises(ValueError):
            self.run_domain([self.names[1]])
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.adapter.execution_count, calls)
        self.assertEqual(self.run_domain([self.names[0]])["tasks"][0]["status"], "skipped")
        self.assertEqual(self.adapter.execution_count, calls)

    def test_direct_task_rejects_another_tasks_summary(self):
        schedule.run_task(self.names[0], 1, **self.options)
        before, calls = self.persisted(), self.adapter.execution_count
        with self.assertRaises(ValueError):
            schedule.run_task(self.names[1], 1, **self.options)
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.adapter.execution_count, calls)

    def test_locked_summary_merge_does_not_adopt_other_task_modes(self):
        summary = self.root / "summary.json"
        old = {"task_id": self.names[0], "inputs": "same", "modes": {"agg": {"ok": True}}}
        summary.write_text(json.dumps(old))
        before = summary.read_bytes()
        result = schedule.TaskResult(self.names[1], "accounting", 1, inputs="same")
        with self.assertRaises(ValueError):
            schedule._write_summary(summary, result)
        self.assertEqual(summary.read_bytes(), before)

    def test_legacy_summary_without_task_id_still_resumes(self):
        schedule.run_task(self.names[0], 1, **self.options)
        summary = next((self.root / "runs").rglob("summary.json"))
        saved = json.loads(summary.read_text())
        del saved["task_id"]
        summary.write_text(json.dumps(saved))
        calls = self.adapter.execution_count
        self.assertEqual(self.run_domain([self.names[0]])["tasks"][0]["status"], "skipped")
        self.assertEqual(self.adapter.execution_count, calls)

    def test_unambiguous_ids_keep_existing_paths_and_run_normally(self):
        names = ["group/item", "group__other"]
        self.adapter = NamedDemo(self.root, names)
        self.options["adapter"] = self.adapter
        self.assertEqual([r["status"] for r in self.run_domain(names)["tasks"]], ["ok", "ok"])
        self.assertEqual(self.adapter.execution_count, 8)
        namespace = schedule.Namespace("demo", "oc", "agg", 1)
        self.assertEqual(namespace.task_dir(self.root, names[0]).name, "group__item")
        self.assertEqual(len(list((self.root / "runs").rglob("summary.json"))), 2)
        self.assertEqual(len(list((self.root / "library").rglob("EXPERIENCE.md"))), 2)


if __name__ == "__main__":
    unittest.main()
