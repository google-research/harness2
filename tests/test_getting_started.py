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

"""Check real local artifacts and failure behavior of the onboarding example."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.getting_started import ledger
from harness2.core.proposer import Proposer


class GettingStartedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.output = self.root / "output"
        self.console = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.console))
        self.enterContext(
            patch.object(Proposer, "_opencode", side_effect=AssertionError("model call"))
        )
        self.enterContext(
            patch.object(Proposer, "_claude_cli", side_effect=AssertionError("model call"))
        )

    def source(self, name="week-1", content="amount\n100.25\n-20.10\n5.00\n-7.25\n"):
        path = self.inputs / f"{name}.csv"
        path.write_text(content)
        return path

    def summary(self, task="ledger-1", k=1):
        path = self.output / f"runs/demo-oc-agg{k}/{task}/summary.json"
        return json.loads(path.read_text())

    def test_custom_stream_executes_known_totals_and_carries_prior_context(self):
        self.source()
        self.source("week-2", "amount\n45.00\n5.00\n")
        self.source("week-3", "amount\n18.00\n-18.00\n")
        self.assertEqual(ledger.run(self.inputs, self.output, 1, "both"), 0)
        for index, expected in enumerate(("77.90", "50.00", "0.00"), 1):
            summary = self.summary(f"ledger-{index}")
            for mode in ("agg", "seq"):
                selected = Path(summary["selections"][mode]["chosen_run_dir"])
                self.assertEqual((selected / "answer.txt").read_text(), f"Total: {expected}\n")
        manifest = json.loads((self.output / "demo.json").read_text())
        self.assertEqual((manifest["done"], manifest["failed"]), (3, 0))
        self.assertEqual(manifest["tasks"][2]["library_entries_before"], {"agg": 2, "seq": 2})
        context = self.output / "runs/demo-oc-agg1/ledger-3/context/inv1/library/harnesses"
        self.assertEqual(sorted(p.name for p in context.iterdir()), ["ledger-1", "ledger-2"])

    def test_inspection_reads_outputs_without_reexecuting_and_detects_corruption(self):
        self.source()
        ledger.run(self.inputs, self.output, 1, "sequential")
        selected = Path(self.summary()["selections"]["seq"]["chosen_run_dir"])
        answer = selected / "answer.txt"
        with patch.object(ledger.DemoAdapter, "execute", side_effect=AssertionError("execution")):
            self.assertTrue(ledger.inspect(self.output))
            answer.write_text("Total: 999.00\n")
            self.assertFalse(ledger.inspect(self.output))
            answer.unlink()
            with self.assertRaises(FileNotFoundError):
                ledger.inspect(self.output)

    def test_missing_task_summary_is_not_a_successful_comparison(self):
        self.source()
        ledger.run(self.inputs, self.output, 1, "parallel")
        (self.output / "runs/demo-oc-agg1/ledger-1/summary.json").unlink()
        with self.assertRaisesRegex(ValueError, "missing task summaries"):
            ledger.inspect(self.output)

    def test_changed_inputs_cannot_reuse_existing_output(self):
        self.source()
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("existing work")
        with self.assertRaisesRegex(ValueError, "already exists"):
            ledger.run(self.inputs, self.output, 1, "both")
        self.assertEqual(marker.read_text(), "existing work")
        self.assertEqual(list(self.output.iterdir()), [marker])

    def test_invalid_inputs_fail_before_creating_output(self):
        for content in (
            "total\n1.00\n",
            "amount\n",
            "amount\nNaN\n",
            "amount\nInfinity\n",
            "amount\n1.001\n",
            "amount\n1.00,2.00\n",
            "amount\nnot-a-number\n",
        ):
            with self.subTest(content=content):
                self.source(content=content)
                with self.assertRaises(ValueError):
                    ledger.run(self.inputs, self.output, 1, "both")
                self.assertFalse(self.output.exists())

    def test_missing_inputs_and_invalid_budget_fail_before_execution(self):
        with self.assertRaisesRegex(ValueError, "no .*csv files"):
            ledger.run(self.inputs, self.output, 1, "parallel")
        self.source()
        with self.assertRaisesRegex(ValueError, "positive"):
            ledger.run(self.inputs, self.output, 0, "parallel")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
