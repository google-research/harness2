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

"""`.env` values, and the comments that follow them.

`load_dotenv` strips a trailing comment only when the value contains " #", so a
value that is EMPTY and then commented -- `KEY=  # note` -- was set to the comment
text itself. `.env` is the documented configuration surface, so a commented-out
setting became a non-empty garbage value.
"""

import os
import tempfile
import unittest
from pathlib import Path

from harness2 import config

PREFIX = "HARNESS2_DOTENV_TEST_"


class DotenvValueTests(unittest.TestCase):
    def setUp(self):
        for key in list(os.environ):
            if key.startswith(PREFIX):
                del os.environ[key]

    def tearDown(self):
        for key in list(os.environ):
            if key.startswith(PREFIX):
                del os.environ[key]

    def load(self, text: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".env"
            path.write_text(text, encoding="utf-8")
            config.load_dotenv(path)
        return {k: v for k, v in os.environ.items() if k.startswith(PREFIX)}

    def test_an_empty_value_with_a_comment_is_empty(self):
        got = self.load(f"{PREFIX}A=  # the port to bind\n")
        self.assertEqual(got.get(f"{PREFIX}A"), "")

    def test_a_value_that_is_only_a_comment_is_empty(self):
        got = self.load(f"{PREFIX}B=#just a comment\n")
        self.assertEqual(got.get(f"{PREFIX}B"), "")

    def test_a_value_with_a_trailing_comment_keeps_the_value(self):
        got = self.load(f"{PREFIX}C= 8080 # the port to bind\n")
        self.assertEqual(got.get(f"{PREFIX}C"), "8080")

    def test_a_hash_inside_a_value_is_kept(self):
        # only a `#` at the start of the value or after whitespace starts a comment
        got = self.load(f"{PREFIX}D=bar#baz\n")
        self.assertEqual(got.get(f"{PREFIX}D"), "bar#baz")

    def test_a_quoted_value_is_unquoted_and_keeps_its_hash(self):
        got = self.load(f'{PREFIX}E="a # b"\n')
        self.assertEqual(got.get(f"{PREFIX}E"), "a # b")

    def test_a_fully_commented_line_is_skipped(self):
        got = self.load(f"# {PREFIX}F=commented out\n")
        self.assertNotIn(f"{PREFIX}F", got)

    def test_the_shell_still_wins(self):
        os.environ[f"{PREFIX}G"] = "from the shell"
        got = self.load(f"{PREFIX}G=from the file\n")
        self.assertEqual(got.get(f"{PREFIX}G"), "from the shell")


if __name__ == "__main__":
    unittest.main()
