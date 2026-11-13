# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Independent domains must not share a mutable experience library."""

import tempfile
import unittest
from pathlib import Path

from harness2.core import library, schedule
from harness2.demo import DemoAdapter, DemoProposer


class DomainDemo(DemoAdapter):
    def __init__(self, root, domains):
        super().__init__(root)
        self.domains = dict(zip(self.tasks(), domains, strict=True))

    def domain_of(self, task_id):
        return self.domains[task_id]


class DomainPathCollisionTest(unittest.TestCase):
    def test_selected_domains_cannot_alias_the_same_library(self):
        for domains in (
            ("finance/a", "finance__a"),
            ("finance\\a", "finance/a"),
            ("finance / a", "finance/a"),
            ("finance", " finance "),
            ("", "unknown"),
        ):
            with self.subTest(domains=domains), tempfile.TemporaryDirectory() as td:
                adapter = DomainDemo(Path(td), domains)
                with self.assertRaisesRegex(ValueError, "domains.*same library directory"):
                    schedule.task_stream(adapter)
                with self.assertRaisesRegex(ValueError, "domains.*same library directory"):
                    schedule.task_stream(adapter, max_tasks=1)

    def test_rejection_precedes_dispatch_and_preserves_existing_library(self):
        for workers in (1, 2):
            with self.subTest(workers=workers), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                adapter = DomainDemo(root, ("finance/a", "finance__a"))
                prop = DemoProposer(adapter.spec.channels["proposer"])
                kwargs = dict(
                    adapter=adapter,
                    prop=prop,
                    runs_root=root / "runs",
                    library_root=root / "library",
                    modes=("agg",),
                    domain_parallel=workers,
                )
                initial = schedule.run_sweep(1, only_domains=("finance/a",), **kwargs)
                self.assertEqual(initial["domains"][0]["done"], 1)
                lib = library.Library(
                    root / "library" / "agg", "finance/a", adapter.substrate.surface()
                )
                before = {
                    str(path.relative_to(lib.dir)): path.read_bytes()
                    for path in lib.dir.rglob("*")
                    if path.is_file()
                }
                executions = adapter.execution_count
                calls = len(prop._calls)
                with self.assertRaisesRegex(ValueError, "domains.*same library directory"):
                    schedule.run_sweep(1, **kwargs)
                self.assertEqual(adapter.execution_count, executions)
                self.assertEqual(len(prop._calls), calls)
                self.assertEqual(
                    before,
                    {
                        str(path.relative_to(lib.dir)): path.read_bytes()
                        for path in lib.dir.rglob("*")
                        if path.is_file()
                    },
                )

    def test_filters_allow_one_selected_domain(self):
        with tempfile.TemporaryDirectory() as td:
            adapter = DomainDemo(Path(td), ("finance/a", "finance__a"))
            self.assertEqual(
                schedule.task_stream(adapter, only_domains=("finance/a",)),
                {"finance/a": ["ledger-1"]},
            )
            self.assertEqual(
                schedule.task_stream(adapter, only_tasks=("ledger-2",)),
                {"finance__a": ["ledger-2"]},
            )

    def test_normal_domains_keep_separate_experience_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adapter = DomainDemo(root, ("finance/a", "finance/b"))
            prop = DemoProposer(adapter.spec.channels["proposer"])
            kwargs = dict(
                adapter=adapter,
                prop=prop,
                runs_root=root / "runs",
                library_root=root / "library",
                modes=("agg",),
                domain_parallel=2,
            )
            result = schedule.run_sweep(1, **kwargs)
            self.assertEqual(sum(domain["done"] for domain in result["domains"]), 2)
            executions = adapter.execution_count
            calls = len(prop._calls)
            resumed = schedule.run_sweep(1, **kwargs)
            self.assertEqual(adapter.execution_count, executions)
            self.assertEqual(len(prop._calls), calls)
            self.assertTrue(all(d["tasks"][0]["status"] == "skipped" for d in resumed["domains"]))
            for domain, task in (("finance/a", "ledger-1"), ("finance/b", "ledger-2")):
                lib = library.Library(root / "library" / "agg", domain, adapter.substrate.surface())
                self.assertEqual(lib.entries(), [task])

    def test_tasks_in_one_domain_are_not_a_collision(self):
        with tempfile.TemporaryDirectory() as td:
            adapter = DomainDemo(Path(td), ("finance/a", "finance/a"))
            self.assertEqual(schedule.task_stream(adapter), {"finance/a": ["ledger-1", "ledger-2"]})


if __name__ == "__main__":
    unittest.main()
