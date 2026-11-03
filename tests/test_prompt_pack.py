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

"""The prompt pack is a property of the BENCHMARK, not of the recursion mode.

`propose()` used to route on `prompt_pack == "tts" and round_t is None`, and
`compose_round()` had no tts branch at all. So for a tts benchmark the parallel arm
was prompted in the tts pack and the sequential arm in the "adapt" pack as soon as
k >= 2 -- two different instructions and two different surface blocks for the two arms
of the same comparison.
"""

import hashlib
import unittest

from harness2.core import prompts as P
from harness2.core.spec import BenchPrompts
from harness2.substrate.opencode import OPENCODE_SURFACE

TTS_SURFACE = "# EDITABLE HARNESS SURFACE"
ADAPT_SURFACE = "# WHAT YOU CAN CHANGE"

# SHA-256 (truncated) of a prompt's text. A failure here means THAT PROMPT CHANGED:
# the parallel arm must not move -- this PR is about the sequential arm -- and the adapt
# pack's round composer only had its body moved into `_round_body`.
# key: (prompt, first arg, second arg)
UNTOUCHED_PARALLEL = {
    ("propose", 1, "INV"): "8e1a4d1022285e9c",
    ("propose", 1, "DOM"): "c8ca41d5fb5339f7",
    ("propose", 2, "INV"): "11aba375f9b197f8",
    ("propose", 2, "DOM"): "d062877773521110",
    ("propose", 3, "INV"): "198a4c8ee2de011c",
    ("propose", 3, "DOM"): "a5f3c6d3a3b1c15d",
    ("aggregate", 2, ""): "ffbdbe53482c9feb",
}

UNTOUCHED_ADAPT = {
    ("compose_round", 1, 2): "aa40f4335ae518f1",
    ("compose_round", 2, 3): "e516e35fb951756d",
}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class PromptPackTests(unittest.TestCase):
    def setUp(self):
        self.tts = BenchPrompts(work="do the work", prompt_pack="tts")
        self.adapt = BenchPrompts(work="do the work")

    def test_both_arms_use_the_benchmarks_pack_at_every_k(self):
        for k in (1, 2, 3):
            for dim in ("INV", "DOM"):
                parallel = P.propose(self.tts, dim, OPENCODE_SURFACE, k=k, index=1)
                sequential = P.propose(
                    self.tts, dim, OPENCODE_SURFACE, k=k, index=1, round_t=1 if k > 1 else None
                )
                for arm, prompt in (("parallel", parallel), ("sequential", sequential)):
                    self.assertIn(TTS_SURFACE, prompt, f"{arm}/{dim}/k={k}")
                    self.assertNotIn(ADAPT_SURFACE, prompt, f"{arm}/{dim}/k={k}")

    def test_both_composers_use_the_benchmarks_pack(self):
        for prompt in (
            P.aggregate(self.tts, OPENCODE_SURFACE, k=2, index=1),
            P.compose_round(self.tts, OPENCODE_SURFACE, t=1, k=2),
        ):
            self.assertIn(TTS_SURFACE, prompt)
            self.assertNotIn(ADAPT_SURFACE, prompt)

    def test_the_adapt_pack_is_unchanged(self):
        for prompt in (
            P.propose(self.adapt, "INV", OPENCODE_SURFACE, k=2, index=1),
            P.propose(self.adapt, "INV", OPENCODE_SURFACE, k=2, index=1, round_t=1),
            P.compose_round(self.adapt, OPENCODE_SURFACE, t=1, k=2),
        ):
            self.assertIn(ADAPT_SURFACE, prompt)
            self.assertNotIn(TTS_SURFACE, prompt)

    def test_the_parallel_arm_is_byte_identical_to_before(self):
        for k in (1, 2, 3):
            for dim in ("INV", "DOM"):
                prompt = P.propose(self.tts, dim, OPENCODE_SURFACE, k=k, index=1)
                self.assertEqual(_digest(prompt), UNTOUCHED_PARALLEL[("propose", k, dim)])
        aggregate = P.aggregate(self.tts, OPENCODE_SURFACE, k=2, index=1)
        self.assertEqual(_digest(aggregate), UNTOUCHED_PARALLEL[("aggregate", 2, "")])

    def test_the_adapt_round_composer_is_byte_identical_to_before(self):
        for t, k in ((1, 2), (2, 3)):
            prompt = P.compose_round(self.adapt, OPENCODE_SURFACE, t=t, k=k)
            self.assertEqual(_digest(prompt), UNTOUCHED_ADAPT[("compose_round", t, k)])

    def test_the_sequential_arm_carries_the_round_contract(self):
        for dim in ("INV", "DOM"):
            sequential = P.propose(self.tts, dim, OPENCODE_SURFACE, k=2, index=1, round_t=1)
            self.assertIn("This is round 1", sequential)
            self.assertIn("KEEP_BASE", sequential)
            # the rollouts stay the BASE rollouts in every mode; the checkpoint's own
            # rollout reaches the stage in the pool.
            self.assertIn("under the current (base) harness", sequential)
            parallel = P.propose(self.tts, dim, OPENCODE_SURFACE, k=2, index=1)
            self.assertNotIn("This is round", parallel)

    def test_the_round_composer_keeps_the_checkpoint_semantics(self):
        composer = P.compose_round(self.tts, OPENCODE_SURFACE, t=1, k=2)
        self.assertIn("KEEP_BASE", composer)
        self.assertIn("CURRENT checkpoint", composer)
        self.assertIn("<notes>", composer)


if __name__ == "__main__":
    unittest.main()
