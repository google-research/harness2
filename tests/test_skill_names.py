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

"""Skill name spelling must agree with the substrate's YAML frontmatter value."""

import unittest
from unittest.mock import Mock

from harness2.core import recursion, tree
from harness2.substrate.codex import CODEX_SURFACE
from harness2.substrate.opencode import OPENCODE_SURFACE


class SkillNameTests(unittest.TestCase):
    def candidate(self, surface, name_line):
        return {
            surface.always_on[0]: "Use invoice-review when reviewing invoices.",
            f"{surface.skill_dir}/invoice-review/SKILL.md": (
                f"---\n{name_line}description: Review invoices.\n---\nCheck invoice totals.\n"
            ),
        }

    def test_matching_name_spellings_are_accepted(self):
        for surface in (CODEX_SURFACE, OPENCODE_SURFACE):
            for value in ("invoice-review", '"invoice-review"', "'invoice-review'"):
                with self.subTest(surface=surface.name, value=value):
                    census = tree.validate(surface, self.candidate(surface, f"name: {value}\n"))
                    self.assertEqual(census["skills"], 1)

    def test_other_names_and_unbalanced_quotes_are_rejected(self):
        for surface in (CODEX_SURFACE, OPENCODE_SURFACE):
            for value in (
                "other-skill",
                '"other-skill"',
                "'other-skill'",
                '"invoice-review',
                "invoice-review'",
                "\"invoice-review'",
            ):
                with self.subTest(surface=surface.name, value=value):
                    with self.assertRaisesRegex(ValueError, "frontmatter"):
                        tree.validate(surface, self.candidate(surface, f"name: {value}\n"))

    def test_name_remains_optional_only_for_codex(self):
        self.assertEqual(
            tree.validate(CODEX_SURFACE, self.candidate(CODEX_SURFACE, ""))["skills"], 1
        )
        with self.assertRaisesRegex(ValueError, "matching its directory"):
            tree.validate(OPENCODE_SURFACE, self.candidate(OPENCODE_SURFACE, ""))

    def test_quoted_skill_is_accepted_as_a_proposal(self):
        adapter = Mock()
        adapter.spec.allow_subagents = True
        adapter.spec.reserved_scripts = frozenset()
        candidate = self.candidate(CODEX_SURFACE, 'name: "invoice-review"\n')
        fallback = {"AGENTS.md": candidate["AGENTS.md"]}

        kept, error = recursion._accept_tree(CODEX_SURFACE, adapter, candidate, fallback, "inv1")

        self.assertFalse(kept)
        self.assertEqual(error, "")
        adapter.validate_tree.assert_called_once_with(candidate)


if __name__ == "__main__":
    unittest.main()
