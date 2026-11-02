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

"""The aggregate NOTES FORMAT example count must be a possible fraction at every k."""

import re
import unittest

from harness2.core import prompts
from harness2.core.spec import BenchPrompts

_FRACTION = re.compile(r"(\d+)/(\d+) base rollouts")


class AggregateCountTest(unittest.TestCase):
    def _example(self, k: int) -> tuple[int, int]:
        bp = BenchPrompts(work="reconcile the ledger", prompt_pack="adapt")
        text = prompts.aggregate(bp, "opencode", k=k)
        m = _FRACTION.search(text)
        self.assertIsNotNone(m, f"aggregate(k={k}) lost its 'N/N base rollouts' example")
        return int(m.group(1)), int(m.group(2))

    def test_k1_example_is_a_possible_fraction(self):
        # agg-1 (parallel, k=1) is the default sweep; its aggregate prompt must not
        # tell the composer to cite "2/1 base rollouts" -- more failures than rollouts.
        numerator, denominator = self._example(1)
        self.assertEqual(denominator, 1)
        self.assertLessEqual(
            numerator,
            denominator,
            f"aggregate(k=1) example cites {numerator}/{denominator} base rollouts",
        )

    def test_example_numerator_never_exceeds_k(self):
        for k in (1, 2, 3, 5):
            numerator, denominator = self._example(k)
            self.assertEqual(denominator, k)
            self.assertLessEqual(
                numerator,
                k,
                f"aggregate(k={k}) example cites {numerator}/{k} base rollouts",
            )
            self.assertGreaterEqual(numerator, 1)


if __name__ == "__main__":
    unittest.main()
