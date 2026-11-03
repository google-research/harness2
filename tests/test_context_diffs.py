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

import tempfile
import unittest
from pathlib import Path

from harness2.core import context, schedule, tree
from harness2.demo import BASE_SCRIPT, DemoAdapter, DemoProposer


class ContextDiffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.surface = self.adapter.substrate.surface()
        self.candidate = self.root / "candidate"
        self.adopted = self.root / "adopted"
        for path, condition in ((self.candidate, ""), (self.adopted, " if amount < 0")):
            harness = tree.read_tree(self.surface, self.adapter.base_harness())
            harness["scripts/summarize.py"] = BASE_SCRIPT.replace(" if amount > 0", condition)
            tree.write_tree(self.surface, harness, path, stubs=False)

    def test_reflection_diff_uses_base_while_preserving_adopted_harness(self):
        evidence = context.build(
            self.root / "reflection",
            self.adapter,
            "ledger-1",
            harness_dir=self.adopted,
            base_harness_dir=self.adapter.base_harness(),
            pool=[("inv1", self.candidate, None), ("unchanged", self.adapter.base_harness(), None)],
        )
        script = "scripts/summarize.py"
        diff = (evidence.root / "pool/inv1/harness.diff").read_text()
        self.assertIn(f"--- context/base_harness/{script}\n", diff)
        self.assertIn(f"+++ context/pool/inv1/harness/{script}\n", diff)
        self.assertIn("-total = sum((amount for amount in amounts if amount > 0)", diff)
        self.assertIn("+total = sum((amount for amount in amounts)", diff)
        self.assertNotIn("if amount < 0", diff)
        self.assertIn("if amount < 0", (evidence.root / "harness" / script).read_text())
        self.assertEqual((evidence.root / "base_harness" / script).read_text(), BASE_SCRIPT)
        self.assertEqual((evidence.root / "pool/unchanged/harness.diff").read_text(), "")
        self.assertIn("context/base_harness/", evidence.layout)
        self.assertIn("harness.diff", evidence.layout)

    def test_stage_diff_uses_current_checkpoint_for_history(self):
        evidence = context.build(
            self.root / "stage",
            self.adapter,
            "ledger-1",
            harness_dir=self.candidate,
            pool=[("base1", self.adapter.base_harness(), None), ("inv2", self.candidate, None)],
        )
        diff = (evidence.root / "pool/base1/harness.diff").read_text()
        self.assertIn("--- context/harness/scripts/summarize.py\n", diff)
        self.assertIn("+++ context/pool/base1/harness/scripts/summarize.py\n", diff)
        self.assertIn("+total = sum((amount for amount in amounts if amount > 0)", diff)
        self.assertIn("-total = sum((amount for amount in amounts)", diff)
        self.assertEqual((evidence.root / "pool/inv2/harness.diff").read_text(), "")
        self.assertFalse((evidence.root / "base_harness").exists())
        self.assertIn("harness.diff", evidence.layout)

    def test_explicit_base_must_exist_and_pass_evidence_checks(self):
        with self.assertRaises(FileNotFoundError):
            context.build(
                self.root / "missing-base",
                self.adapter,
                "ledger-1",
                harness_dir=self.adopted,
                base_harness_dir=self.root / "missing",
            )
        (self.adapter.base_harness() / "reference_answer.txt").write_text("answer key")
        with self.assertRaisesRegex(ValueError, "answer-key path"):
            context.build(
                self.root / "leaking-base",
                self.adapter,
                "ledger-1",
                harness_dir=self.adopted,
                base_harness_dir=self.adapter.base_harness(),
            )

    def test_real_workflow_keeps_edit_visible_in_both_reflection_modes(self):
        proposer = DemoProposer(self.adapter.spec.channels["proposer"])
        result = schedule.run_domain(
            "accounting",
            ["ledger-1"],
            2,
            adapter=self.adapter,
            prop=proposer,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "library",
        )
        self.assertEqual((result["done"], result["failed"]), (1, 0))
        for mode in ("agg", "seq"):
            work = self.adapter.spec.runs_root / f"demo-oc-{mode}2/ledger-1"
            for suffix in ("", ".experience"):
                with self.subTest(mode=mode, suffix=suffix):
                    reflection = work / "reflection" / f"{mode}2{suffix}"
                    diff = (reflection / "pool/inv1/harness.diff").read_text()
                    self.assertIn("--- context/base_harness/scripts/summarize.py\n", diff)
                    self.assertIn("-total = sum((amount for amount in amounts if amount > 0)", diff)
                    self.assertIn("+total = sum((amount for amount in amounts)", diff)
                    self.assertEqual(
                        (reflection / "harness/scripts/summarize.py").read_text(),
                        (reflection / "pool/inv1/harness/scripts/summarize.py").read_text(),
                    )
        sequential = self.adapter.spec.runs_root / "demo-oc-seq2/ledger-1"
        self.assertEqual((sequential / "context/inv2/pool/c1/harness.diff").read_text(), "")


if __name__ == "__main__":
    unittest.main()
