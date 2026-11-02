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

"""A skill counts as triggered only when the always-on text names it as a whole name."""

import unittest

from harness2.core import tree
from harness2.substrate.codex import CODEX_SURFACE
from harness2.substrate.opencode import OPENCODE_SURFACE


def skill(name):
    return f"---\nname: {name}\ndescription: totals in a ledger\n---\nbody\n"


class SkillTriggerTests(unittest.TestCase):
    def assert_untriggered_then_repaired(self, t, name):
        with self.assertRaisesRegex(ValueError, f"skill '{name}' is never named"):
            tree.validate(OPENCODE_SURFACE, dict(t))
        self.assertEqual(tree.autotrigger(OPENCODE_SURFACE, t), [name])
        tree.validate(OPENCODE_SURFACE, t)

    def test_name_inside_a_word_is_not_a_trigger(self):
        t = {
            "systemprompt.md": "Write the document carefully.\n",
            "skills/doc/SKILL.md": skill("doc"),
        }
        self.assert_untriggered_then_repaired(t, "doc")

    def test_name_inside_a_sibling_skill_name_is_not_a_trigger(self):
        t = {
            "systemprompt.md": "When totals matter, load the `check_totals` skill.\n",
            "skills/check/SKILL.md": skill("check"),
            "skills/check_totals/SKILL.md": skill("check_totals"),
        }
        self.assert_untriggered_then_repaired(t, "check")

    def test_trigger_line_forms_still_count(self):
        for surface, prompt in (
            (OPENCODE_SURFACE, "systemprompt.md"),
            (CODEX_SURFACE, "AGENTS.md"),
        ):
            with self.subTest(surface=prompt):
                line = surface.trigger_line.format(name="doc", desc="a ledger is open")
                t = {prompt: line, f"{surface.skill_dir}/doc/SKILL.md": skill("doc")}
                self.assertEqual(tree.autotrigger(surface, t), [])
                tree.validate(surface, t)


if __name__ == "__main__":
    unittest.main()
