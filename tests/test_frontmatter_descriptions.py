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

"""YAML frontmatter boundary and block-scalar description checks for skills and subagents."""

import tempfile
import unittest
from pathlib import Path

from harness2.core import tree
from harness2.substrate.codex import CODEX_SURFACE
from harness2.substrate.opencode import OPENCODE_SURFACE, compose_prompt


class FrontmatterDescriptionTests(unittest.TestCase):
    def test_folded_and_literal_block_scalars_expand_in_autotrigger_and_compose(self):
        for indicator in (">-", ">", "|", "|-"):
            with self.subTest(indicator=indicator):
                candidate = {
                    "systemprompt.md": "Solve the accounting task.",
                    "guardrails.md": "",
                    "LongTermMEMORY.md": "",
                    "tool_descriptions/tool_guidance.md": "",
                    "skills/arabic-invoice-audit/SKILL.md": (
                        "---\n"
                        "name: arabic-invoice-audit\n"
                        f"description: {indicator}\n"
                        "  مراجعة الفواتير ومطابقة الضرائب\n"
                        "  قبل تسليم التقرير النهائي.\n"
                        "---\n"
                        "# Body\n"
                    ),
                }
                repaired = tree.autotrigger(OPENCODE_SURFACE, candidate)
                self.assertEqual(repaired, ["arabic-invoice-audit"])
                self.assertIn(
                    "- When مراجعة الفواتير ومطابقة الضرائب قبل تسليم التقرير النهائي, "
                    "load the `arabic-invoice-audit` skill.",
                    candidate["guardrails.md"],
                )
                self.assertNotIn("When >", candidate["guardrails.md"])
                self.assertEqual(tree.validate(OPENCODE_SURFACE, candidate)["skills"], 1)

                with tempfile.TemporaryDirectory() as tmp:
                    staged = tree.write_tree(OPENCODE_SURFACE, candidate, Path(tmp), stubs=False)
                    composed = compose_prompt(staged)
                    self.assertIn(
                        "- `arabic-invoice-audit`: مراجعة الفواتير ومطابقة الضرائب "
                        "قبل تسليم التقرير النهائي.",
                        composed,
                    )

    def test_empty_description_does_not_swallow_following_frontmatter_key(self):
        candidate = {
            "AGENTS.md": "Use invoice-review when reviewing invoices.",
            ".agents/skills/invoice-review/SKILL.md": (
                "---\n"
                "description:\n"
                "name: invoice-review\n"
                "---\n"
                "Check totals.\n"
            ),
        }
        with self.assertRaisesRegex(ValueError, "needs a `description:`"):
            tree.validate(CODEX_SURFACE, candidate)

        auto_tree = {
            "AGENTS.md": "Solve the task.",
            ".agents/skills/invoice-review/SKILL.md": candidate[
                ".agents/skills/invoice-review/SKILL.md"
            ],
        }
        tree.autotrigger(CODEX_SURFACE, auto_tree)
        self.assertIn(
            "- When the invoice-review workflow applies, "
            "read .agents/skills/invoice-review/SKILL.md",
            auto_tree["AGENTS.md"],
        )

    def test_empty_block_scalar_and_missing_frontmatter_fences_are_rejected(self):
        empty_block = {
            "AGENTS.md": "Use invoice-review when reviewing invoices.",
            ".agents/skills/invoice-review/SKILL.md": (
                "---\nname: invoice-review\ndescription: >-\n---\nBody\n"
            ),
        }
        with self.assertRaisesRegex(ValueError, "needs a `description:`"):
            tree.validate(CODEX_SURFACE, empty_block)

        body_only = {
            "AGENTS.md": "Use invoice-review when reviewing invoices.",
            ".agents/skills/invoice-review/SKILL.md": (
                "# Invoice Review\ndescription: Looks like frontmatter in body prose.\n"
            ),
        }
        with self.assertRaisesRegex(ValueError, "needs a `description:`"):
            tree.validate(CODEX_SURFACE, body_only)

    def test_utf8_bom_and_frontmatter_past_600_chars_are_accepted(self):
        long_comment = "# " + ("x" * 700) + "\n"
        candidate = {
            "systemprompt.md": "Load invoice-review when needed.",
            "guardrails.md": "",
            "LongTermMEMORY.md": "",
            "tool_descriptions/tool_guidance.md": "",
            "skills/invoice-review/SKILL.md": (
                "\ufeff---\n"
                + long_comment
                + "name: invoice-review\n"
                + 'description: "Reconcile multi-currency invoices."\n'
                + "---\n"
                + "Check totals.\n"
            ),
            "sub_agents/auditor/AGENT.md": (
                "\ufeff---\n"
                + long_comment
                + "mode: subagent\n"
                + "description: >-\n"
                + "  Audit extracted line items in an isolated context.\n"
                + "---\n"
                + "Return concise findings.\n"
            ),
        }
        census = tree.validate(OPENCODE_SURFACE, candidate)
        self.assertEqual(census["skills"], 1)
        self.assertEqual(census["sub_agents"], 1)


if __name__ == "__main__":
    unittest.main()
