# SPDX-License-Identifier: Apache-2.0

"""Focused regressions for the paper's execution, evidence, and adoption contracts."""

import tempfile
import unittest
from pathlib import Path

from harness2.core import schedule
from harness2.demo import DemoAdapter, DemoProposer


class SeedNamespaceTests(unittest.TestCase):
    def test_negative_seed_cannot_be_spelled_by_a_tag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = DemoAdapter(root)
            proposer = DemoProposer(adapter.spec.channels["proposer"])

            def run(**identity):
                return schedule.run_domain(
                    "accounting",
                    [adapter.tasks()[0]],
                    1,
                    adapter=adapter,
                    prop=proposer,
                    runs_root=adapter.spec.runs_root,
                    library_root=root / "library",
                    **identity,
                )

            first = run(seed=-1)
            self.assertEqual(first["tasks"][0]["status"], "ok")
            executions = adapter.execution_count
            tagged = run(tag="s-1")
            self.assertEqual(tagged["done"], 0, tagged)
            self.assertEqual(tagged["failed"], 1, tagged)
            self.assertIn("spells a seed segment", tagged["tasks"][0]["error"])
            self.assertEqual(adapter.execution_count, executions)

    def test_signed_seed_prefixes_are_reserved(self):
        for tag in ("s-1", "s-23-trial", "s0", "s23-trial"):
            with self.subTest(tag=tag):
                with self.assertRaisesRegex(ValueError, "spells a seed segment"):
                    schedule.ns("demo", "oc", "agg", 1, tag=tag)

    def test_negative_seed_and_unrelated_tag_control(self):
        self.assertEqual(schedule.ns("demo", "oc", "agg", 1, seed=-1), "demo-oc-agg1-s-1")
        self.assertEqual(
            schedule.ns("demo", "oc", "agg", 1, tag="sample-1"), "demo-oc-agg1-sample-1"
        )
