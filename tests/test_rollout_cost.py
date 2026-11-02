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

import json
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.core import schedule
from harness2.core.spec import rollout_cost
from harness2.demo import DemoAdapter, DemoProposer
from harness2.substrate.base import stream_cost_of
from harness2.substrate.codex import CodexSubstrate
from harness2.substrate.opencode import OpencodeSubstrate


def _step(cost) -> str:
    return json.dumps({"type": "step_finish", "part": {"type": "step-finish", "cost": cost}})


class PricedDemoAdapter(DemoAdapter):
    """The demo executor, charging a flat price per rollout like an OpenCode stream would."""

    PRICE = 0.25

    def rollout_cost(self, run_dir: Path) -> float | None:
        return self.PRICE


class FreeDemoAdapter(DemoAdapter):
    """A priced executor whose model costs nothing: $0, but not unpriced."""

    def rollout_cost(self, run_dir: Path) -> float | None:
        return 0.0


class UnpricedDemoAdapter(DemoAdapter):
    """Like Codex: the stream carries tokens, never a price."""

    def rollout_cost(self, run_dir: Path) -> float | None:
        return None


class BrokenCostAdapter(DemoAdapter):
    def rollout_cost(self, run_dir: Path) -> float | None:
        raise OSError("stream vanished")


class StreamCostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_opencode_sums_step_finish_costs_over_every_attempt(self):
        a1 = self.root / "attempt1.jsonl"
        a2 = self.root / "attempt2.jsonl"
        a1.write_text(
            "\n".join(
                [
                    "banner line",
                    _step(0.10),
                    json.dumps({"type": "text", "part": {"type": "text", "text": "hi"}}),
                    _step(0.05),
                    "{not json",
                ]
            ),
            encoding="utf-8",
        )
        a2.write_text(_step(0.2) + "\n", encoding="utf-8")
        oc = OpencodeSubstrate()
        self.assertAlmostEqual(oc.stream_cost([a1, a2]), 0.35)
        self.assertEqual(oc.stream_cost([self.root / "missing.jsonl"]), None)
        self.assertEqual(oc.stream_cost([]), None)

    def test_opencode_tokens_without_a_price_are_unpriced_not_free(self):
        def step(cost, tokens):
            return json.dumps(
                {"type": "step_finish", "part": {"type": "step-finish", "cost": cost, **tokens}}
            )

        used = {"tokens": {"input": 1200, "output": 300, "reasoning": 0, "cache": {"read": 50}}}
        oc = OpencodeSubstrate()
        cases = {
            "unregistered model": ([step(0, used), step(0, used)], None),
            "no tokens used": ([step(0, {"tokens": {"input": 0, "output": 0}})], 0.0),
            "priced": ([step(0.02, used), step(0.01, used)], 0.03),
        }
        for name, (lines, want) in cases.items():
            with self.subTest(name):
                stream = self.root / f"{name}.jsonl"
                stream.write_text("\n".join(lines), encoding="utf-8")
                got = oc.stream_cost([stream])
                if want is None:
                    self.assertIsNone(got)
                else:
                    self.assertAlmostEqual(got, want)

    def test_codex_and_unpriced_substrates_report_no_cost(self):
        stream = self.root / "codex.jsonl"
        stream.write_text(_step(1.0), encoding="utf-8")
        self.assertIsNone(CodexSubstrate().stream_cost([stream]))
        self.assertIsNone(stream_cost_of(object(), [stream]))

    def test_jobbench_counts_every_attempt_of_a_rollout(self):
        adapter = JobBenchAdapter.__new__(JobBenchAdapter)
        adapter.store = self.root / "store"
        adapter.substrate = OpencodeSubstrate()
        run_dir = adapter.store / "acct" / "task1" / "model_output" / "base1"
        traj = adapter._traj_dir("acct/task1", "base1")
        traj.mkdir(parents=True)
        (traj / "task1_base1_attempt1_x.jsonl").write_text(_step(0.4), encoding="utf-8")
        (traj / "task1_base1_attempt2_y.jsonl").write_text(_step(0.6), encoding="utf-8")
        self.assertAlmostEqual(adapter.rollout_cost(run_dir), 1.0)
        self.assertEqual(rollout_cost(adapter, run_dir), 1.0)

    def test_rollout_cost_keeps_unpriced_apart_from_free(self):
        class Returns:
            def __init__(self, value):
                self.value = value

            def rollout_cost(self, run_dir):
                return self.value

        for value in (None, -1.0, "0.5", float("nan"), float("inf"), True):
            with self.subTest(value=value):
                self.assertIsNone(rollout_cost(Returns(value), self.root))
        self.assertIsNone(rollout_cost(object(), self.root))
        self.assertIsNone(rollout_cost(BrokenCostAdapter.__new__(BrokenCostAdapter), self.root))
        self.assertEqual(rollout_cost(Returns(0), self.root), 0.0)
        self.assertEqual(rollout_cost(Returns(2), self.root), 2.0)


class LedgerRolloutSpendTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _run(self, adapter, k):
        proposer = DemoProposer(adapter.spec.channels["proposer"])
        return schedule.run_domain(
            "accounting",
            adapter.tasks(),
            k,
            adapter=adapter,
            prop=proposer,
            runs_root=adapter.spec.runs_root,
            library_root=self.root / "library" / str(k),
        )

    def _ledgers(self, adapter, k):
        for task_id in adapter.tasks():
            summary_path = adapter.spec.runs_root / f"demo-oc-agg{k}" / task_id / "summary.json"
            summary = json.loads(summary_path.read_text())
            for mode in ("agg", "seq"):
                yield summary["modes"][mode]["ledger"]

    def test_every_rollout_is_priced_in_the_ledger(self):
        for k in (1, 2):
            with self.subTest(k=k):
                adapter = PricedDemoAdapter(self.root / f"k{k}")
                result = self._run(adapter, k)
                self.assertEqual((result["done"], result["failed"]), (2, 0))
                for ledger in self._ledgers(adapter, k):
                    self.assertEqual(ledger["R"], 4 * k)
                    self.assertEqual(ledger["unpriced_rollouts"], 0)
                    self.assertAlmostEqual(ledger["rollout_usd"], 4 * k * adapter.PRICE)
                    self.assertAlmostEqual(
                        ledger["usd"], ledger["rollout_usd"] + ledger["session_usd"]
                    )

    def test_free_unpriced_and_failing_hooks_are_told_apart(self):
        for name, cls, unpriced in (
            ("free", FreeDemoAdapter, 0),
            ("unpriced", UnpricedDemoAdapter, 4),
            ("broken", BrokenCostAdapter, 4),
            ("no hook", DemoAdapter, 4),
        ):
            with self.subTest(adapter=name):
                adapter = cls(self.root / name)
                result = self._run(adapter, 1)
                self.assertEqual((result["done"], result["failed"]), (2, 0))
                for ledger in self._ledgers(adapter, 1):
                    self.assertEqual(ledger["rollout_usd"], 0.0)
                    self.assertEqual(ledger["unpriced_rollouts"], unpriced)


if __name__ == "__main__":
    unittest.main()
