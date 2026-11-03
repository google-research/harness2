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

"""The selector prompt presents its criteria as one ordered list, in every conformance mode.

`selection_conformance` chooses what occupies position 2: the full CONFORMANCE block, or the
value-only tail that replaces it. Each mode must still render 1..6 with no missing step and no
step beyond the list, because the heading says the criteria are applied "IN THIS ORDER".
"""

import re
import unittest

from harness2.core.prompts import select
from harness2.core.spec import BenchPrompts


def numbers(text: str) -> list[int]:
    """The criteria numbers rendered at line starts, in order."""
    return [int(n) for n in re.findall(r"^(\d+)\. ", text, re.M)]


def prompt(conformance: str, **kwargs) -> str:
    bp = BenchPrompts(
        work="Produce a reconciliation workbook.", selection_conformance=conformance, **kwargs
    )
    return select(bp, 2, "finals")


class SelectorCriteriaTests(unittest.TestCase):
    def test_full_conformance_renders_one_contiguous_list(self) -> None:
        self.assertEqual(numbers(prompt("full")), [1, 2, 3, 4, 5, 6])

    def test_value_only_conformance_renders_one_contiguous_list(self) -> None:
        text = prompt("value_only")

        self.assertEqual(numbers(text), [1, 2, 3, 4, 5, 6])
        self.assertIn("2. VALUES ARE VALUES.", text)

    def test_value_only_drops_the_conformance_block_but_keeps_the_value_rule(self) -> None:
        text = prompt("value_only")

        self.assertNotIn("CONFORMANCE.", text)
        self.assertIn("PRESENT AS A VALUE", text)

    def test_full_keeps_the_conformance_block_and_its_value_rule(self) -> None:
        text = prompt("full")

        self.assertIn("2. CONFORMANCE.", text)
        self.assertIn("PRESENT AS A VALUE", text)

    def test_a_selection_lead_is_added_before_the_list(self) -> None:
        self.assertEqual(
            numbers(prompt("value_only", selection_lead="Open the task first.")),
            [0, 1, 2, 3, 4, 5, 6],
        )


if __name__ == "__main__":
    unittest.main()
