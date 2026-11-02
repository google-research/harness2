# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
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

"""Reject matcher types that make Codex discard an entire hooks file."""

import json
import unittest

from harness2.core import tree
from harness2.substrate.codex import CODEX_SURFACE


class HookMatcherValidationTests(unittest.TestCase):
    def proposed(self, entries):
        return {
            "AGENTS.md": "Follow the task instructions.",
            ".codex/hooks.json": json.dumps({"hooks": {"PostToolUse": entries}}),
        }

    def group(self, **fields):
        return {"hooks": [{"type": "command", "command": "echo ok"}], **fields}

    def test_rejects_non_string_matchers(self):
        for matcher in (True, False, 42, 1.5, [], ["Bash"], {}, {"tool": "Bash"}):
            with self.subTest(matcher=matcher):
                proposed = self.proposed([self.group(matcher=matcher)])
                with self.assertRaisesRegex(
                    ValueError,
                    r"\.codex/hooks\.json: PostToolUse\[0\].*matcher.*string or null",
                ):
                    tree.validate(CODEX_SURFACE, proposed)

    def test_accepts_missing_null_and_string_matchers_without_rewriting(self):
        groups = [self.group()]
        groups.extend(self.group(matcher=value) for value in (None, "", "Bash", "Bash|Edit"))
        for group in groups:
            with self.subTest(group=group):
                proposed = self.proposed([group])
                original = proposed.copy()
                self.assertEqual(tree.validate(CODEX_SURFACE, proposed)["hooks"], 1)
                self.assertEqual(proposed, original)

    def test_reports_invalid_group_among_valid_siblings(self):
        proposed = self.proposed([self.group(matcher="Bash"), self.group(matcher=42), self.group()])
        with self.assertRaisesRegex(ValueError, r"PostToolUse\[1\].*matcher"):
            tree.validate(CODEX_SURFACE, proposed)


if __name__ == "__main__":
    unittest.main()
