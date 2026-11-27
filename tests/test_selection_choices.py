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
from types import SimpleNamespace

from harness2.core import selection
from harness2.core.proposer import Reply
from harness2.core.spec import RunView
from harness2.demo import DemoAdapter


class SelectionChoiceTests(unittest.TestCase):
    def choose(self, text):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        adapter = DemoAdapter(root)
        candidates = {}
        for label in ("agg1", "agg2"):
            run_dir = adapter.execute("ledger-1", None, label)
            candidates[label] = RunView(label, label, run_dir, ok=True, delivered=True)
        proposer = SimpleNamespace(
            call=lambda *args: Reply("select.agg2", text, True, cost_usd=0.5)
        )
        result = selection.run_selection(
            "ledger-1",
            2,
            "agg",
            candidates,
            adapter=adapter,
            prop=proposer,
            work_dir=root / "work",
            pool_kind="finals",
        )
        saved = json.loads((root / "work/selection/agg2/selection.json").read_text())
        return result, saved

    def test_conflicting_choices_are_a_selector_failure(self):
        for choices in (("A", "B"), ("B", "A"), ("A", "unknown")):
            with self.subTest(choices=choices):
                text = "\nCorrection:\n".join(f"```choice\n{choice}\n```" for choice in choices)
                result, saved = self.choose(text)
                self.assertFalse(
                    result.ok, "conflicting choices were accepted as a model selection"
                )
                self.assertTrue(result.fallback)
                self.assertIn("conflicting", result.error)
                self.assertEqual(result.usd, 0.5)
                self.assertEqual(saved["ok"], result.ok)
                self.assertEqual(saved["error"], result.error)
                self.assertEqual(saved["chosen_label"], "agg1")

    def test_single_and_repeated_identical_choices_remain_valid(self):
        for choices in (("A",), ("B",), ("B", "B")):
            with self.subTest(choices=choices):
                text = "\n".join(f"```choice\n{choice}\n```" for choice in choices)
                result, saved = self.choose(text)
                self.assertTrue(result.ok)
                self.assertFalse(result.fallback)
                self.assertEqual(result.chosen_id, choices[0])
                self.assertEqual(saved["chosen_id"], choices[0])

    def test_missing_and_unknown_choices_keep_the_existing_failure_path(self):
        for text in ("No decision.", "```choice\nunknown\n```"):
            with self.subTest(text=text):
                result, _ = self.choose(text)
                self.assertFalse(result.ok)
                self.assertTrue(result.fallback)
