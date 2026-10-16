# SPDX-License-Identifier: Apache-2.0
"""Resume must respect the evidence prefix that produced each task's adaptation."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.core import schedule
from harness2.core.proposer import ProposerError
from harness2.demo import DemoAdapter, DemoProposer


class LibraryResumeTests(unittest.TestCase):
    def test_recovered_prior_selection_invalidates_later_empty_library_run(self):
        class RecoveringSelector(DemoProposer):
            recovering = False

            def call(self, mission, context_dir, label, **kwargs):
                if (
                    not self.recovering
                    and label.startswith("select.")
                    and Path(context_dir, "source/ledger.csv")
                    .read_text()
                    .startswith("amount\n100.25")
                ):
                    raise ProposerError("temporary selector failure", attempts=1)
                return super().call(mission, context_dir, label, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = DemoAdapter(root)
            proposer = RecoveringSelector(adapter.spec.channels["proposer"])

            def run():
                return schedule.run_domain(
                    "accounting",
                    adapter.tasks(),
                    2,
                    adapter=adapter,
                    prop=proposer,
                    runs_root=adapter.spec.runs_root,
                    library_root=root / "library",
                )

            first = run()
            self.assertEqual([row["status"] for row in first["tasks"]], ["partial", "ok"])
            later_context = adapter.spec.runs_root / "demo-oc-agg2/ledger-2/context/inv1/library"
            self.assertFalse(later_context.exists())
            proposer.recovering = True
            repaired = run()
            self.assertEqual([row["status"] for row in repaired["tasks"]], ["ok", "ok"])
            self.assertTrue((later_context / "harnesses/ledger-1/EXPERIENCE.md").is_file())
            resumed = run()
            self.assertEqual([row["status"] for row in resumed["tasks"]], ["skipped", "skipped"])

    def test_legacy_task_only_cache_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = DemoAdapter(root)
            proposer = DemoProposer(adapter.spec.channels["proposer"])

            def run():
                return schedule.run_task(
                    "ledger-1",
                    1,
                    adapter=adapter,
                    prop=proposer,
                    runs_root=adapter.spec.runs_root,
                )

            self.assertEqual(run().status, "ok")
            summary = adapter.spec.runs_root / "demo-oc-agg1/ledger-1/summary.json"
            old = json.loads(summary.read_text())
            for selection in old["selections"].values():
                selection.pop("input_library_fingerprint", None)
            summary.write_text(json.dumps(old))
            count = adapter.execution_count
            self.assertEqual(run().status, "skipped")
            self.assertEqual(adapter.execution_count, count)
