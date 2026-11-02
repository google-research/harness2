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

"""Every rendered sub-agent file must declare mode: subagent."""

import re
import unittest

from harness2.benchmarks.jobbench import _ensure_mode_subagent


def _has_mode(text: str) -> bool:
    return bool(re.search(r"(?m)^mode:\s*subagent", text))


class EnsureModeSubagentTests(unittest.TestCase):
    def test_plain_front_matter_gets_mode(self):
        out = _ensure_mode_subagent("---\ndescription: x\n---\nBody\n")
        self.assertTrue(_has_mode(out))
        self.assertEqual(out, "---\nmode: subagent\ndescription: x\n---\nBody\n")

    def test_leading_blank_line_front_matter_gets_mode(self):
        # The guard uses text.lstrip().startswith("---"), so it recognises front matter
        # after a leading blank line; the insertion must add the mode there too.
        out = _ensure_mode_subagent("\n---\ndescription: x\n---\nBody\n")
        self.assertTrue(_has_mode(out))

    def test_crlf_front_matter_gets_mode(self):
        out = _ensure_mode_subagent("---\r\ndescription: x\r\n---\r\nBody\r\n")
        self.assertTrue(_has_mode(out))

    def test_already_has_mode_is_unchanged(self):
        text = "---\nmode: subagent\ndescription: x\n---\nBody\n"
        self.assertEqual(_ensure_mode_subagent(text), text)

    def test_no_front_matter_is_wrapped(self):
        out = _ensure_mode_subagent("Just a body\n")
        self.assertTrue(_has_mode(out))
        self.assertTrue(out.startswith("---\n"))


if __name__ == "__main__":
    unittest.main()
