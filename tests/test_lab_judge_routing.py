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

"""The LAB judge can be pointed at an OpenAI-compatible endpoint (#95).

The judge client itself lives in the patched upstream dependency, so what this
repo owns is the contract: the patch adds the routing, and the adapter keeps
the credentials the judge subprocess needs.
"""

import unittest
from pathlib import Path

from harness2.benchmarks.lab import _judge_keys

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "third_party/patches/harvey-labs-judge-vertex-anthropic.diff"


class LabJudgeRoutingTests(unittest.TestCase):
    def test_patch_adds_the_openai_compatible_branch(self):
        patch = PATCH.read_text(encoding="utf-8")
        self.assertIn('startswith("openai-compatible/")', patch)
        self.assertIn("chat.completions.create", patch)
        # the constraint ladder: json_object steering on all but the last attempt
        self.assertIn('"response_format"', patch)

    def test_openai_compatible_judge_keeps_its_credentials(self):
        self.assertEqual(
            _judge_keys("openai-compatible/deepseek-flash"),
            ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
        )


if __name__ == "__main__":
    unittest.main()
