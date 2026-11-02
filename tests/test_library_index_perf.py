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

"""Tests for Library.rebuild_index O(M) scaling and wb_render._entries caching."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks import wb_render
from harness2.core import tree as harness_tree
from harness2.core.library import Library, _inventory
from harness2.demo import DemoAdapter


class LibraryRebuildIndexPerfTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.surface = self.adapter.substrate.surface()

    def test_rebuild_index_uses_experience_components_without_reading_tree(self):
        lib = Library(self.root / "library", "accounting", self.surface)
        harness = harness_tree.read_tree(self.surface, self.adapter.base_harness())

        # Add two entries
        lib.add(
            "task-1",
            harness,
            why="good",
            label="c1",
            experience={"cue": "cue-1", "lesson": "lesson-1", "evidence": "ev-1"},
        )
        lib.add(
            "task-2",
            harness,
            why="better",
            label="c2",
            experience={"cue": "cue-2", "lesson": "lesson-2", "evidence": "ev-2"},
        )

        expected_inv = _inventory(self.surface, harness)
        self.assertIn(expected_inv, lib.index.read_text(encoding="utf-8"))

        # When rebuild_index runs, read_tree should NOT be called because components is in EXPERIENCE.md
        with patch.object(harness_tree, "read_tree") as mock_read:
            lib.rebuild_index()
            self.assertEqual(mock_read.call_count, 0)

        # Index still contains accurate content
        content = lib.index.read_text(encoding="utf-8")
        self.assertIn("## `harnesses/task-1/`", content)
        self.assertIn(f"- harness: `harnesses/task-1/harness/` — {expected_inv}", content)
        self.assertIn("- cue:     cue-1", content)
        self.assertIn("## `harnesses/task-2/`", content)
        self.assertIn(f"- harness: `harnesses/task-2/harness/` — {expected_inv}", content)
        self.assertIn("- cue:     cue-2", content)


class WbRenderEntriesCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.trial = self.root / "trial_1"
        self.raw = self.trial / "verifier" / "raw_artifacts"
        self.raw.mkdir(parents=True)
        (self.raw / "report.txt").write_text("Revenue: $1000\n", encoding="utf-8")

    def test_entries_is_cached_across_repeated_calls(self):
        wb_render._cached_entries.cache_clear()
        with patch.object(wb_render, "_entries_dir", wraps=wb_render._entries_dir) as mock_dir:
            res1 = wb_render._entries(self.trial, "office")
            res2 = wb_render._entries(self.trial, "office")
            chars = wb_render.body_chars(self.trial, "office")
            rendered = wb_render.render(self.trial, "office")

            self.assertEqual(mock_dir.call_count, 1)
            self.assertEqual(res1, res2)
            self.assertEqual(chars, len("Revenue: $1000"))
            self.assertIn("report.txt", rendered)


if __name__ == "__main__":
    unittest.main()
