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

"""Offline regressions for improvement outages and successful fallback resumes."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.core import recursion, schedule, tree
from harness2.core.proposer import ProposerError, Reply
from harness2.demo import DemoAdapter, DemoProposer


class FirstCandidateSelector(DemoProposer):
    """Allow selection to succeed even when every final uses the base harness."""

    def call(self, mission, context_dir, label, **kwargs):
        if label.startswith("select."):
            chosen = sorted((Path(context_dir) / "candidates").glob("*.md"))[0].stem
            reply = Reply(label, f"```choice\n{chosen}\n```", True, attempts=1)
            self._calls.append(reply)
            return reply
        return super().call(mission, context_dir, label, **kwargs)


class OutageProposer(DemoProposer):
    def call(self, mission, context_dir, label, **kwargs):
        raise ProposerError("offline outage", attempts=2, cost_usd=0.25)


class ProposerFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.spec = self.adapter.spec.channels["proposer"]

    def run_stream(self, k, proposer, selector):
        return schedule.run_domain(
            "accounting",
            self.adapter.tasks(),
            k,
            adapter=self.adapter,
            prop=proposer,
            selector_prop=selector,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "library" / str(k),
        )

    def summary_path(self, k, task_id):
        return self.adapter.spec.runs_root / f"demo-oc-agg{k}" / task_id / "summary.json"

    def test_outage_is_partial_then_healthy_rerun_succeeds_and_skips(self):
        for k in (1, 2):
            with self.subTest(k=k):
                selector = FirstCandidateSelector(self.spec)
                executions = self.adapter.execution_count
                failed = self.run_stream(k, OutageProposer(self.spec), selector)
                self.assertEqual((failed["done"], failed["failed"]), (0, 2))
                self.assertEqual([row["status"] for row in failed["tasks"]], ["partial"] * 2)
                for task_id in self.adapter.tasks():
                    summary = json.loads(self.summary_path(k, task_id).read_text())
                    self.assertEqual(summary["status"], "partial")
                    self.assertEqual(len(summary["errors"]), 6 * k)
                    for mode in ("agg", "seq"):
                        record = summary["modes"][mode]
                        self.assertFalse(record["ok"])
                        self.assertTrue(summary["selections"][mode]["ok"])
                        for stage in record["stages"]:
                            self.assertTrue(stage["execution_failed"])
                            self.assertTrue(stage["fell_back"])
                            self.assertFalse(stage["cached"])
                            self.assertEqual((stage["attempts"], stage["usd"]), (2, 0.25))
                            self.assertIn(
                                f"{mode}: {stage['label']}: proposer execution failed: offline outage",
                                summary["errors"],
                            )
                        ledger = record["ledger"]
                        self.assertEqual((ledger["R"], ledger["P"]), (4 * k, 3 * k))
                        self.assertEqual(ledger["session_attempts"], 6 * k)
                        self.assertEqual(ledger["session_usd"], 0.75 * k)
                        chosen = Path(summary["selections"][mode]["chosen_run_dir"])
                        self.assertTrue(self.adapter.delivered(chosen))
                # Every fallback still ran: k shared bases and 3k rollouts per mode/task.
                self.assertEqual(self.adapter.execution_count - executions, 14 * k)
                proposer = DemoProposer(self.spec)
                executions = self.adapter.execution_count
                healthy = self.run_stream(k, proposer, proposer)
                self.assertEqual([row["status"] for row in healthy["tasks"]], ["ok"] * 2)
                self.assertEqual(self.adapter.execution_count - executions, 14 * k)
                for task_id in self.adapter.tasks():
                    summary = json.loads(self.summary_path(k, task_id).read_text())
                    self.assertEqual(summary["status"], "ok")
                    self.assertEqual(summary["errors"], [])
                    for mode in ("agg", "seq"):
                        record = summary["modes"][mode]
                        self.assertTrue(record["ok"])
                        self.assertTrue(all(not s["execution_failed"] for s in record["stages"]))
                        self.assertEqual(record["ledger"]["session_attempts"], 3 * k)
                        self.assertEqual(record["ledger"]["session_usd"], 0)
                executions, calls = self.adapter.execution_count, len(proposer._calls)
                resumed = self.run_stream(k, proposer, proposer)
                self.assertEqual([row["status"] for row in resumed["tasks"]], ["skipped"] * 2)
                self.assertEqual(
                    (self.adapter.execution_count, len(proposer._calls)), (executions, calls)
                )

    def test_one_failed_stage_only_fails_its_mode(self):
        for k in (1, 2):
            for mode in ("agg", "seq"):
                for label in (f"inv{k}", f"dom{k}", f"agg{k}" if mode == "agg" else f"c{k}"):
                    with self.subTest(k=k, mode=mode, label=label):
                        adapter = DemoAdapter(self.root / f"{mode}{k}-{label}")

                        class OneOutage(DemoProposer):
                            def call(self, mission, context_dir, call_label, **kwargs):
                                if (
                                    f"demo-oc-{mode}{k}" in Path(context_dir).parts
                                    and call_label == f"{label}.a1"
                                ):
                                    raise ProposerError(
                                        "one stage failed", attempts=2, cost_usd=0.25
                                    )
                                return super().call(mission, context_dir, call_label, **kwargs)

                        result = schedule.run_task(
                            "ledger-1",
                            k,
                            adapter=adapter,
                            prop=OneOutage(self.spec),
                            selector_prop=FirstCandidateSelector(self.spec),
                            runs_root=adapter.spec.runs_root,
                        )
                        self.assertEqual(result.status, "partial")
                        self.assertEqual(
                            result.errors,
                            [f"{mode}: {label}: proposer execution failed: one stage failed"],
                        )
                        for recorded_mode, record in result.modes.items():
                            self.assertEqual(record["ok"], recorded_mode != mode)
                            self.assertEqual(
                                [s["label"] for s in record["stages"] if s["execution_failed"]],
                                [label] if recorded_mode == mode else [],
                            )
                            self.assertTrue(result.selections[recorded_mode]["ok"])
                            self.assertEqual(record["ledger"]["R"], 4 * k)
                            self.assertEqual(record["ledger"]["P"], 3 * k)

    def test_rejected_edits_keep_successful_fallback_and_resume(self):
        class InvalidEdits(DemoProposer):
            def call(self, mission, context_dir, label, **kwargs):
                workspace = Path(context_dir) / "harness"
                (workspace / "scripts/summarize.py").write_text("def broken(:\n")
                reply = Reply(
                    label, "invalid edit", True, cost_usd=0.25, attempts=1, workspace=workspace
                )
                self._calls.append(reply)
                return reply

        for k in (1, 2):
            with self.subTest(k=k):
                proposer, selector = InvalidEdits(self.spec), FirstCandidateSelector(self.spec)
                result = self.run_stream(k, proposer, selector)
                self.assertEqual([row["status"] for row in result["tasks"]], ["ok"] * 2)
                for task_id in self.adapter.tasks():
                    summary = json.loads(self.summary_path(k, task_id).read_text())
                    self.assertEqual(summary["errors"], [])
                    for record in summary["modes"].values():
                        for stage in record["stages"]:
                            self.assertTrue(stage["fell_back"])
                            self.assertFalse(stage["execution_failed"])
                            self.assertTrue(stage["error"])
                            self.assertEqual((stage["attempts"], stage["usd"]), (2, 0.5))
                        self.assertEqual(record["ledger"]["session_attempts"], 6 * k)
                        self.assertEqual(record["ledger"]["session_usd"], 1.5 * k)
                executions = self.adapter.execution_count
                calls = (len(proposer._calls), len(selector._calls))
                resumed = self.run_stream(k, proposer, selector)
                self.assertEqual([row["status"] for row in resumed["tasks"]], ["skipped"] * 2)
                self.assertEqual(self.adapter.execution_count, executions)
                self.assertEqual((len(proposer._calls), len(selector._calls)), calls)

    def test_rejected_edit_then_outage_preserves_both_attempts_and_costs(self):
        class InvalidThenOutage(DemoProposer):
            def call(self, mission, context_dir, label, **kwargs):
                if label.endswith(".a2"):
                    raise ProposerError("offline outage", attempts=2, cost_usd=0.25)
                workspace = Path(context_dir) / "harness"
                (workspace / "scripts/summarize.py").write_text("def broken(:\n")
                return Reply(
                    label, "invalid edit", True, cost_usd=0.5, attempts=1, workspace=workspace
                )

        fallback = tree.read_tree(self.adapter.substrate.surface(), self.adapter.base_harness())
        stage = recursion._run_stage(
            label="inv1",
            dim="INV",
            job="Repair the script",
            fallback=fallback,
            adapter=self.adapter,
            prop=InvalidThenOutage(self.spec),
            ctx_dir=self.root / "context",
            task_id="ledger-1",
            rollouts=[],
            harness_dir=self.adapter.base_harness(),
            library_dir=None,
        )
        self.assertTrue(stage.execution_failed)
        self.assertTrue(stage.fell_back)
        self.assertEqual(stage.tree, fallback)
        self.assertEqual((stage.attempts, stage.usd, stage.cached), (3, 0.75, False))

    def test_legacy_ok_summary_without_discriminator_still_skips(self):
        proposer = DemoProposer(self.spec)
        self.run_stream(1, proposer, proposer)
        for task_id in self.adapter.tasks():
            path = self.summary_path(1, task_id)
            summary = json.loads(path.read_text())
            for record in summary["modes"].values():
                for stage in record["stages"]:
                    stage.pop("execution_failed")
                    stage["error"] = "offline outage"
            path.write_text(json.dumps(summary))
        executions, calls = self.adapter.execution_count, len(proposer._calls)
        resumed = self.run_stream(1, proposer, proposer)
        self.assertEqual([row["status"] for row in resumed["tasks"]], ["skipped"] * 2)
        self.assertEqual((self.adapter.execution_count, len(proposer._calls)), (executions, calls))


if __name__ == "__main__":
    unittest.main()
