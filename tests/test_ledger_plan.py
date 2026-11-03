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

"""The budget identity is a set of STAGES, not a pair of counts.

`assert_identity` used to check that R == 4k and P == 3k and that no label
repeated. A run that recorded the right NUMBER of stages under the wrong names
passed: the count could not see that a candidate the ledger counts was never
actually produced.
"""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.core import schedule
from harness2.core.ledger import BudgetError, Ledger, expected_labels
from harness2.demo import DemoAdapter, DemoProposer


def _ledger(mode: str, k: int, *, labels: list[str] | None = None) -> Ledger:
    """A ledger with the plan's labels, or the given ones."""
    rollouts, sessions = expected_labels(mode, k)
    led = Ledger(mode=mode, task_id="t", k=k)
    for label in sorted(rollouts if labels is None else labels):
        led.rollout(label)
    for label in sorted(sessions if labels is None else labels):
        led.session(label)
    return led


class BudgetPlanTests(unittest.TestCase):
    def test_the_plan_is_the_stages_the_recursion_runs(self):
        rollouts, sessions = expected_labels("agg", 2)
        self.assertEqual(
            rollouts, {"base1", "base2", "inv1", "inv2", "dom1", "dom2", "agg1", "agg2"}
        )
        self.assertEqual(sessions, {"inv1", "inv2", "dom1", "dom2", "agg1", "agg2"})
        rollouts, sessions = expected_labels("seq", 2)
        self.assertEqual(
            rollouts, {"base1", "base2", "inv1", "inv2", "dom1", "dom2", "c1", "c2"}
        )
        self.assertEqual(sessions, {"inv1", "inv2", "dom1", "dom2", "c1", "c2"})

    def test_a_run_of_the_plan_satisfies_the_identity(self):
        for mode in ("agg", "seq"):
            for k in (1, 2, 3):
                with self.subTest(mode=mode, k=k):
                    _ledger(mode, k).assert_identity()

    def test_the_right_counts_under_the_wrong_names_do_not(self):
        # R and P are both right; the composer wrote "c1" where the plan says "agg1".
        led = _ledger("agg", 1, labels=["base1", "inv1", "dom1", "c1"])
        with self.assertRaises(BudgetError) as caught:
            led.assert_identity()
        message = str(caught.exception)
        self.assertIn("missing ['agg1']", message)
        self.assertIn("unexpected ['c1']", message)

    def test_a_repeated_label_still_fails(self):
        led = _ledger("agg", 1, labels=["base1", "inv1", "dom1", "agg1"])
        led.rollout("agg1")
        with self.assertRaises(BudgetError) as caught:
            led.assert_identity()
        self.assertIn("DUPLICATE labels", str(caught.exception))

    def test_a_short_run_fails(self):
        led = _ledger("agg", 1, labels=["base1", "inv1", "dom1"])
        with self.assertRaises(BudgetError):
            led.assert_identity()

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            expected_labels("adaptive", 2)

    def test_the_demo_run_satisfies_the_plan(self):
        # The real thing: a sweep's own ledgers, checked against the plan.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adapter = DemoAdapter(root)
            proposer = DemoProposer(adapter.spec.channels["proposer"])
            for k in (1, 2):
                with self.subTest(k=k):
                    schedule.run_domain(
                        "accounting",
                        adapter.tasks(),
                        k,
                        adapter=adapter,
                        prop=proposer,
                        runs_root=adapter.spec.runs_root,
                        library_root=root / "library" / str(k),
                    )
                    summary = json.loads(
                        (
                            adapter.spec.runs_root
                            / f"demo-oc-agg{k}"
                            / adapter.tasks()[0]
                            / "summary.json"
                        ).read_text()
                    )
                    for mode in ("agg", "seq"):
                        ledger = Ledger(
                            mode=mode, task_id=adapter.tasks()[0], k=k
                        )
                        recorded = summary["modes"][mode]["ledger"]
                        for label in recorded["rollouts"]:
                            ledger.rollout(label)
                        for label in recorded["sessions"]:
                            ledger.session(label)
                        ledger.assert_identity()


if __name__ == "__main__":
    unittest.main()
