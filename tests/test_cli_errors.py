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

"""The judge and report CLIs print a misconfiguration as one line, not a traceback."""

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from harness2.cli import judge, report


class CliErrorTests(unittest.TestCase):
    def assert_error_line(self, main, argv, expected):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(main(argv), 1)
        self.assertTrue(stderr.getvalue().startswith("error: "), stderr.getvalue())
        self.assertIn(expected, stderr.getvalue())

    def test_unknown_mode(self):
        for main in (judge.main, report.main):
            with self.subTest(main=main.__module__):
                self.assert_error_line(main, ["--bench", "lab", "--mode", "bogus"], "bogus")

    def test_missing_model_setting(self):
        with patch.dict(os.environ, {"HARNESS2_SOLVER_MODEL": ""}):
            self.assert_error_line(judge.main, ["--bench", "lab"], "HARNESS2_SOLVER_MODEL")

    def test_wb_without_subset(self):
        self.assert_error_line(report.main, ["--bench", "wb"], "--subset")


if __name__ == "__main__":
    unittest.main()
