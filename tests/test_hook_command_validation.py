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

"""Validate hook command types before a proposed Codex harness is accepted."""

import json
import unittest

from harness2.core import tree
from harness2.substrate.codex import CODEX_SURFACE


def harness(hooks):
    return {
        "AGENTS.md": "Follow the task instructions.",
        ".codex/hooks.json": json.dumps({"hooks": hooks}),
    }


def group(*handlers):
    return {"matcher": "Bash", "hooks": list(handlers)}


class HookCommandValidationTests(unittest.TestCase):
    def test_non_string_commands_are_rejected(self):
        for command in (None, True, False, 42, 1.5, [], ["echo", "ok"], {}, {"shell": "echo ok"}):
            with self.subTest(command=command):
                proposed = harness(
                    {"PostToolUse": [group({"type": "command", "command": command})]}
                )
                with self.assertRaisesRegex(ValueError, "command"):
                    tree.validate(CODEX_SURFACE, proposed)

    def test_missing_command_is_rejected(self):
        proposed = harness({"PostToolUse": [group({"type": "command"})]})
        with self.assertRaisesRegex(ValueError, "command"):
            tree.validate(CODEX_SURFACE, proposed)

    def test_empty_and_whitespace_commands_are_rejected(self):
        for command in ("", " ", "\t\n"):
            with self.subTest(command=command):
                proposed = harness(
                    {"PostToolUse": [group({"type": "command", "command": command})]}
                )
                with self.assertRaisesRegex(ValueError, "empty `command`"):
                    tree.validate(CODEX_SURFACE, proposed)

    def test_valid_commands_are_accepted_across_events_and_groups(self):
        proposed = harness(
            {
                event: [
                    group(
                        {"type": "command", "command": "echo hook"},
                        {"type": "command", "command": "  echo café  "},
                    ),
                    group({"type": "command", "command": "python scripts/check.py"}),
                ]
                for event in sorted(CODEX_SURFACE.hook_events)
            }
        )
        original = proposed[".codex/hooks.json"]
        tree.validate(CODEX_SURFACE, proposed)
        self.assertEqual(proposed[".codex/hooks.json"], original)

    def test_invalid_command_is_not_hidden_by_valid_sibling_hooks(self):
        proposed = harness(
            {
                "PostToolUse": [
                    group(
                        {"type": "command", "command": "echo before"},
                        {"type": "command", "command": {"shell": "echo invalid"}},
                        {"type": "command", "command": "echo after"},
                    )
                ],
                "Stop": [group({"type": "command", "command": "echo done"})],
            }
        )
        with self.assertRaisesRegex(ValueError, "PostToolUse.*command"):
            tree.validate(CODEX_SURFACE, proposed)


if __name__ == "__main__":
    unittest.main()
