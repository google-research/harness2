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

"""Answer-key text that arrives inside an execution never reaches a model, and the audit
finds it, and what it propagated, in sweeps that ran without the tripwire."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.cli import report as report_cli
from harness2.core import audit, context, schedule
from harness2.core.answer_key import AnswerKey
from harness2.core.spec import RunView
from harness2.demo import DemoAdapter, DemoProposer

RUBRIC = (
    "The reconciliation reports the signed total of every ledger row including credits, "
    "states it to two decimal places, and names each credit that a positive only filter "
    "would have silently dropped from the sum."
)
FETCH = "https://huggingface.co/datasets/example/ledger-bench/resolve/main/RUBRICS.json"


def carries_rubric(root: Path) -> list[str]:
    needle = "names each credit that a positive only filter"
    return [
        p.relative_to(root).as_posix()
        for p in sorted(Path(root).rglob("*"))
        if p.is_file() and needle in p.read_text(encoding="utf-8", errors="replace")
    ]


class KeyedAdapter(DemoAdapter):
    """The demo with a rubric, and an executor that fetches it on the labels in `leaks`."""

    leaks: tuple[tuple[str, str], ...] = ()

    def answer_key(self, task_id):
        return {"RUBRICS.json rubric 1": RUBRIC}

    def execute(self, task_id, harness_dir, run_id, *, timeout=None):
        run_dir = super().execute(task_id, harness_dir, run_id, timeout=timeout)
        if any(run_id.endswith(f"/{t}/{label}") for t, label in self.leaks):
            with (run_dir / "trajectory.md").open("a", encoding="utf-8") as out:
                out.write(f"[ACTION] curl -sL {FETCH}\n[OBSERVATION]\n")
                out.write(json.dumps({"rubrics": [{"criterion": [RUBRIC]}]}) + "\n")
        return run_dir


class WatchingProposer(DemoProposer):
    """Records every model session whose evidence carried the rubric."""

    def __init__(self, spec):
        super().__init__(spec)
        self.saw_key: list[str] = []

    def call(self, mission, context_dir, label, **kwargs):
        if context_dir and carries_rubric(Path(context_dir)):
            self.saw_key.append(label)
        return super().call(mission, context_dir, label, **kwargs)


class AnswerKeyMatchTests(unittest.TestCase):
    def test_verbatim_and_json_escaped_text_is_found_without_echoing_it(self):
        key = AnswerKey.build("t", {"rubric 1": RUBRIC})
        for text in (
            f"prefix {RUBRIC} suffix",
            json.dumps({"criterion": [RUBRIC.replace(", ", ",\n")]}),
        ):
            hit = key.find(text)
            self.assertIsNotNone(hit)
            self.assertEqual(hit.source, "rubric 1")
            self.assertNotIn("positive only", hit.describe())

    def test_short_overlap_and_paraphrase_are_not_hits(self):
        key = AnswerKey.build("t", {"r": RUBRIC})
        self.assertIsNone(key.find("The reconciliation reports the signed total of every row."))
        self.assertIsNone(
            key.find("Report the signed sum of all ledger rows, credits included, to 2 dp.")
        )

    def test_a_document_one_criterion_quotes_is_not_the_key(self):
        criteria = {
            "c1": RUBRIC,
            "c2": "The memo lists every counterparty whose consent is required before closing "
            "and the clause of the agreement that requires it, with the notice period.",
            "c3": "The memo recommends a filing timeline that starts the waiting period before "
            "the financing condition lapses and explains the order of the two steps.",
        }
        key = AnswerKey.build("t", criteria)
        self.assertIsNone(key.find(f"Exhibit B. {criteria['c2']} End of exhibit."))
        hit = key.find(f"{criteria['c1']}\n{criteria['c3']}")
        self.assertEqual((hit.sources, hit.of), (2, 3))

    def test_text_the_agent_was_shown_and_quoted_passages_are_not_key(self):
        shown = AnswerKey.build("t", {"r": RUBRIC}, visible=[f"Task: {RUBRIC}"])
        self.assertFalse(shown)
        quoted = AnswerKey.build("t", {"r": f'Quotes the clause "{RUBRIC}" exactly.'})
        self.assertFalse(quoted)


class AnswerKeyFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = KeyedAdapter(self.root)
        self.proposer = WatchingProposer(self.adapter.spec.channels["proposer"])

    def run_stream(self, k=1):
        return schedule.run_domain(
            "accounting",
            self.adapter.tasks(),
            k,
            adapter=self.adapter,
            prop=self.proposer,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "library" / str(k),
        )

    def summary(self, task_id, k=1):
        path = self.adapter.spec.runs_root / f"demo-oc-agg{k}" / task_id / "summary.json"
        return json.loads(path.read_text())

    def test_a_base_that_fetched_its_rubric_stops_the_task_before_any_model(self):
        self.adapter.leaks = (("ledger-1", "base1"),)
        result = self.run_stream()
        self.assertEqual([row["status"] for row in result["tasks"]], ["contaminated", "ok"])
        self.assertEqual(self.proposer.saw_key, [])
        work = self.adapter.spec.runs_root / "demo-oc-agg1" / "ledger-1"
        self.assertFalse((work / "context").exists())
        summary = self.summary("ledger-1")
        self.assertIn("base1: trajectory carries", summary["errors"][0])
        self.assertNotIn("positive only", json.dumps(summary))
        again = self.run_stream()
        self.assertEqual(again["tasks"][0]["status"], "contaminated")
        self.assertEqual(self.proposer.saw_key, [])

    def test_a_probe_that_fetched_its_rubric_is_withheld_and_the_task_goes_on(self):
        self.adapter.leaks = (("ledger-1", "inv1"),)
        result = self.run_stream()
        self.assertEqual([row["status"] for row in result["tasks"]], ["ok", "ok"])
        self.assertEqual(self.proposer.saw_key, [])
        summary = self.summary("ledger-1")
        for mode in ("agg", "seq"):
            self.assertEqual(summary["modes"][mode]["fell_back"], [])
        work = self.adapter.spec.runs_root / "demo-oc-seq1" / "ledger-1"
        self.assertTrue(
            (work / "context/c1/pool/inv1/rollout.md").read_text().startswith("(withheld:")
        )
        runs = [
            RunView(label, rid, self.root / "executions" / rid, True, True)
            for label, rid in (
                ("base1", "demo-oc-agg1/ledger-1/base1"),
                ("inv1", "demo-oc-seq1/ledger-1/inv1"),
            )
        ]
        built = context.build(self.root / "probe", self.adapter, "ledger-1", runs)
        self.assertEqual((built.rollouts, built.withheld), (["base1"], ["inv1"]))
        self.assertIn("rollouts withheld: inv1", built.layout)
        with self.assertRaisesRegex(RuntimeError, "inv1: withheld"):
            context.build(self.root / "probe", self.adapter, "ledger-1", runs[1:])

    def test_key_text_by_any_other_route_trips_the_wire(self):
        harness = self.root / "carrying"
        harness.mkdir()
        (harness / "systemprompt.md").write_text(f"Checklist: {RUBRIC}\n")
        with self.assertRaisesRegex(ValueError, "systemprompt.md.*arrived by another route"):
            context.build(self.root / "ctx", self.adapter, "ledger-1", harness_dir=harness)

    def test_a_final_that_fetched_its_rubric_is_never_selected(self):
        self.adapter.leaks = (("ledger-1", "agg1"),)
        self.run_stream()
        self.assertEqual(self.proposer.saw_key, [])
        selections = self.summary("ledger-1")["selections"]
        self.assertFalse(selections["agg"]["ok"])
        self.assertTrue(selections["agg"]["dropped"][0].startswith("agg1 (withheld:"))
        self.assertEqual(selections["seq"]["chosen_label"], "c1")

    def test_audit_traces_a_leak_through_the_library_of_an_unguarded_sweep(self):
        self.adapter.leaks = (("ledger-1", "base1"),)
        with (
            patch.object(context, "assert_no_answer_key_text"),
            patch.object(context, "answer_key_runs", return_value={}),
        ):
            self.run_stream()
        self.assertTrue(self.proposer.saw_key)
        audits = audit.audit_namespace(
            self.adapter, self.adapter.spec.runs_root, "demo-oc-agg1", k=1
        )
        got = {a.task_id: a for a in audits}
        first, second = got["ledger-1"], got["ledger-2"]
        self.assertEqual((first.verdict, second.verdict), ("contaminated", "inherits"))
        self.assertIn("base1", first.executions)
        self.assertTrue(first.model_context["agg"])
        self.assertEqual(second.inherited["agg"], ["ledger-1"])
        self.assertEqual(first.fetches, [FETCH])

        ns_dir = self.adapter.spec.runs_root / "demo-oc-agg1"
        audit.write(audits, ns_dir / audit.FILENAME)
        warning = report_cli._audit_warning(ns_dir, 1)
        self.assertIn("2 task(s) (ledger-1, ledger-2)", warning[0])
        self.assertNotIn("positive only", (ns_dir / audit.FILENAME).read_text())
        self.assertIn("inherit one through the library", audit.render(audits))

    def test_a_clean_sweep_audits_clean(self):
        self.run_stream()
        audits = audit.audit_namespace(
            self.adapter, self.adapter.spec.runs_root, "demo-oc-agg1", k=1
        )
        self.assertEqual([a.verdict for a in audits], ["clean", "clean"])
        self.assertEqual(report_cli._audit_warning(self.adapter.spec.runs_root, 1), [])


if __name__ == "__main__":
    unittest.main()
