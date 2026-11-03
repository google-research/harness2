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

"""Completion markers must be JSON objects before callers can inspect them."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.core import runmark


class RunmarkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.marker = self.root / runmark.MARK

    def assert_unusable(self):
        self.assertIsNone(runmark.read_mark(self.root))
        self.assertFalse(runmark.is_complete(self.root))
        self.assertFalse(runmark.is_complete(self.root, match_harness=False))
        self.assertNotEqual(runmark.why_not(self.root), "reusable")

    def test_non_object_markers_are_unusable(self):
        for value in (None, False, True, 0, 1, 1.5, "", "complete", [], [1]):
            with self.subTest(value=value):
                self.marker.write_text(json.dumps(value), encoding="utf-8")
                self.assert_unusable()

    def test_missing_and_invalid_json_markers_are_unusable(self):
        self.assert_unusable()
        for text in ("", "{", '{"version":'):
            with self.subTest(text=text):
                self.marker.write_text(text, encoding="utf-8")
                self.assert_unusable()

    def test_valid_marker_remains_reusable(self):
        runmark.mark_complete(self.root, run_id="test-run")
        self.assertEqual(runmark.read_mark(self.root)["run_id"], "test-run")
        self.assertTrue(runmark.is_complete(self.root))
        self.assertTrue(runmark.is_complete(self.root, match_harness=False))
        self.assertEqual(runmark.why_not(self.root), "reusable")

    def test_object_markers_keep_version_and_harness_checks(self):
        for value in (
            {},
            {"version": runmark.VERSION + 1, "harness": "base"},
            {"version": runmark.VERSION, "harness": "different"},
        ):
            with self.subTest(value=value):
                self.marker.write_text(json.dumps(value), encoding="utf-8")
                self.assertEqual(runmark.read_mark(self.root), value)
                self.assertFalse(runmark.is_complete(self.root))
                self.assertNotEqual(runmark.why_not(self.root), "reusable")


if __name__ == "__main__":
    unittest.main()
