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

"""`--max-usd`: a sweep stops starting tasks once its summaries record the limit."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.core import report, schedule
from harness2.demo import DemoAdapter, DemoProposer

PRICE = 0.05


class PricedProposer(DemoProposer):
    """The demo improver, charging a flat price per session."""

    def call(self, *args, **kwargs):
        reply = super().call(*args, **kwargs)
        reply.cost_usd = PRICE
        return reply


class SpendLimitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.prop = PricedProposer(self.adapter.spec.channels["proposer"])
        self.ns_dir = self.adapter.spec.runs_root / "demo-oc-agg1"

    def _sweep(self, max_usd):
        rep = schedule.run_sweep(
            1,
            adapter=self.adapter,
            prop=self.prop,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "library",
            max_usd=max_usd,
        )
        return rep, rep["domains"][0]

    def test_stops_between_tasks_and_resumes_with_a_higher_limit(self):
        rep, dom = self._sweep(0.01)
        self.assertEqual(rep["max_usd"], 0.01)
        self.assertEqual((dom["done"], dom["failed"], dom["spend_limit"]), (1, 0, 1))
        self.assertEqual(
            [(row["task"], row["status"]) for row in dom["tasks"]],
            [("ledger-1", "ok"), ("ledger-2", "spend_limit")],
        )
        # The task that started finished every mode; the stopped one never started.
        first = json.loads((self.ns_dir / "ledger-1/summary.json").read_text())
        self.assertEqual((first["status"], set(first["modes"])), ("ok", {"agg", "seq"}))
        self.assertFalse((self.ns_dir / "ledger-2").exists())
        # Every paid session is in the recorded spend, once.
        spent = schedule.recorded_usd(self.ns_dir)
        self.assertAlmostEqual(spent, PRICE * len(self.prop._calls))

        calls = len(self.prop._calls)
        _, dom = self._sweep(0.01)
        self.assertEqual(dom["spend_limit"], 2)
        self.assertEqual(len(self.prop._calls), calls)

        _, dom = self._sweep(100.0)
        self.assertEqual((dom["done"], dom["failed"]), (2, 0))
        self.assertNotIn("spend_limit", dom)
        self.assertEqual([row["status"] for row in dom["tasks"]], ["skipped", "ok"])
        self.assertAlmostEqual(schedule.recorded_usd(self.ns_dir), 2 * spent)

    def test_without_a_limit_the_sweep_is_unchanged(self):
        rep, dom = self._sweep(None)
        self.assertNotIn("max_usd", rep)
        self.assertNotIn("spend_limit", dom)
        self.assertEqual([row["status"] for row in dom["tasks"]], ["ok", "ok"])

    def test_report_warns_when_modes_cover_different_tasks(self):
        def row(mode, n, n_ungraded=0):
            return report.Summary(mode, 1, n=n, n_ungraded=n_ungraded, score=50.0)

        even = [row("agg", 2), row("seq", 1, n_ungraded=1)]
        self.assertNotIn("different numbers of tasks", report.render(even))
        uneven = [row("agg", 2), row("seq", 1)]
        self.assertIn("parallel k=1 2, sequential k=1 1", report.render(uneven))


if __name__ == "__main__":
    unittest.main()
