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

import io
import os
import tarfile
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

    def test_rebuild_index_falls_back_only_for_missing_components(self):
        lib = Library(self.root / "library", "accounting", self.surface)
        harness = harness_tree.read_tree(self.surface, self.adapter.base_harness())
        for task in ("cached", "legacy"):
            lib.add(
                task,
                harness,
                why="good",
                label="c1",
                experience={"cue": task, "lesson": "lesson", "evidence": "evidence"},
            )
        expected = lib.index.read_text(encoding="utf-8")
        experience_path = lib.entry_dir("legacy") / "EXPERIENCE.md"
        original = experience_path.read_text(encoding="utf-8")

        for components_line in (None, "- components: "):
            with self.subTest(components_line=components_line):
                lines = [
                    line
                    for line in original.splitlines()
                    if not line.startswith("- components:")
                ]
                if components_line is not None:
                    lines.insert(2, components_line)
                experience_path.write_text("\n".join(lines), encoding="utf-8")
                with patch.object(
                    harness_tree, "read_tree", wraps=harness_tree.read_tree
                ) as read_tree:
                    lib.rebuild_index()
                read_tree.assert_called_once_with(
                    self.surface, lib.entry_dir("legacy") / "harness"
                )
                self.assertEqual(lib.index.read_text(encoding="utf-8"), expected)


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

    def test_entries_invalidates_when_artifact_is_rewritten(self):
        wb_render.clear_cache()
        with patch.object(wb_render, "_entries_dir", wraps=wb_render._entries_dir) as mock_dir:
            res1 = wb_render._entries(self.trial, "office")
            self.assertEqual(mock_dir.call_count, 1)
            self.assertIn("Revenue: $1000", res1[0][2])

            # Repeated call without modification hits cache
            res1_cached = wb_render._entries(self.trial, "office")
            self.assertEqual(mock_dir.call_count, 1)
            self.assertEqual(res1, res1_cached)

            # Rewrite the artifact at the same path
            report = self.raw / "report.txt"
            report.write_text("Revenue: $5000\n", encoding="utf-8")
            os.utime(report, (report.stat().st_atime, report.stat().st_mtime + 1))

            # Must re-read the updated content, not serve stale cache
            res2 = wb_render._entries(self.trial, "office")
            self.assertEqual(mock_dir.call_count, 2)
            self.assertIn("Revenue: $5000", res2[0][2])

    def test_entries_invalidates_when_tarball_is_rewritten(self):
        wb_render.clear_cache()
        trial = self.root / "web_trial"
        v = trial / "verifier"
        v.mkdir(parents=True)
        tb = v / "agent.workspace.tar.gz"

        def make_tar(content: bytes):
            bio = io.BytesIO()
            with tarfile.open(fileobj=bio, mode="w:gz") as tf:
                ti = tarfile.TarInfo(name="app.js")
                ti.size = len(content)
                tf.addfile(ti, io.BytesIO(content))
            tb.write_bytes(bio.getvalue())

        make_tar(b"console.log('v1');")
        res1 = wb_render._entries(trial, "web")
        self.assertIn("v1", res1[0][2])

        # Cache hit
        with patch.object(wb_render, "_entries_tar", wraps=wb_render._entries_tar) as mock_tar:
            res1_cached = wb_render._entries(trial, "web")
            self.assertEqual(mock_tar.call_count, 0)
            self.assertEqual(res1, res1_cached)

            # Rewrite tarball at the same path
            make_tar(b"console.log('v2');")
            os.utime(tb, (tb.stat().st_atime, tb.stat().st_mtime + 1))
            res2 = wb_render._entries(trial, "web")
            self.assertEqual(mock_tar.call_count, 1)
            self.assertIn("v2", res2[0][2])


if __name__ == "__main__":
    unittest.main()
