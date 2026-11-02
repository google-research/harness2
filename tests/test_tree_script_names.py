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

"""A top-level script may be named with a hyphen, like every other component.

`_NAME` -- the shared component-name pattern used for skills, sub-agents and plugins --
allows `-`, and the proposer's own rejection message tells the model that component names
may contain "lowercase letters, digits, `_` or `-`" with `scripts/...` as an example. The
top-level `scripts/` matcher must honour that same rule, or a proposal that ships a
hyphenated script is silently dropped and then reported as missing.
"""

import unittest

from harness2.core import tree
from harness2.substrate.codex import CODEX_SURFACE as S


class ScriptHyphenNameTest(unittest.TestCase):
    def test_hyphenated_top_level_script_is_on_surface(self):
        # Skills, sub-agents and skill-local scripts already accept a hyphen; a
        # top-level script is a component on the same surface and must too.
        self.assertTrue(tree.is_allowed(S, f"{S.skill_dir}/data-prep/SKILL.md"))
        self.assertTrue(tree.is_allowed(S, "sub_agents/data-prep/AGENT.md"))
        self.assertTrue(tree.is_allowed(S, f"{S.skill_dir}/x/scripts/data-prep.py"))
        self.assertTrue(tree.is_allowed(S, "scripts/data-prep.py"))

    def test_validate_accepts_a_shipped_hyphenated_script(self):
        # A harness that ships scripts/data-prep.py and invokes it from always-on text
        # is complete: validate must not report the file it carries as missing.
        ao = S.always_on[0]
        harness = {
            ao: "Run python scripts/data-prep.py to prepare the ledger.\n",
            "scripts/data-prep.py": "print('ok')\n",
        }
        census = tree.validate(S, harness, allow_subagents=True)
        self.assertEqual(census["scripts"], 1)


if __name__ == "__main__":
    unittest.main()
