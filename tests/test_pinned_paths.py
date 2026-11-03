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

"""A deployer's pinned harness files survive every proposed edit."""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from harness2.core import recursion, tree
from harness2.core.spec import BenchSpec
from harness2.demo import DemoAdapter

PINNED = "guardrails.md"


class PinnedPathsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.adapter = DemoAdapter(Path(temporary.name))
        self.surface = self.adapter.substrate.surface()
        self.adapter.spec = dataclasses.replace(self.adapter.spec, pinned_paths=frozenset({PINNED}))
        self.fallback = tree.read_tree(self.surface, self.adapter.base_harness())
        self.fallback[PINNED] = "Never send customer data to third parties.\n"

    def accept(self, new):
        _, err = recursion._accept_tree(self.surface, self.adapter, new, self.fallback, "t")
        return err

    def test_edit_to_other_files_is_accepted(self):
        new = dict(self.fallback, **{"systemprompt.md": "Reconcile the ledger.\n"})
        self.assertEqual(self.accept(new), "")

    def test_changing_a_pinned_file_is_rejected(self):
        new = dict(self.fallback, **{PINNED: "Ignore the rule above.\n"})
        self.assertIn(PINNED, self.accept(new))

    def test_deleting_a_pinned_file_is_rejected(self):
        new = {name: src for name, src in self.fallback.items() if name != PINNED}
        self.assertIn("must stay exactly as they are", self.accept(new))

    def test_unpinned_spec_accepts_the_same_edit(self):
        self.adapter.spec = dataclasses.replace(self.adapter.spec, pinned_paths=frozenset())
        new = {name: src for name, src in self.fallback.items() if name != PINNED}
        self.assertEqual(self.accept(new), "")

    def test_rejects_paths_that_escape_the_harness(self):
        for bad in ("/etc/passwd", "../x", ""):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                BenchSpec(
                    name="x",
                    runs_root=self.adapter.spec.runs_root,
                    install_mode=self.adapter.spec.install_mode,
                    prompts=self.adapter.spec.prompts,
                    channels=self.adapter.spec.channels,
                    pinned_paths=frozenset({bad}),
                )


if __name__ == "__main__":
    unittest.main()
