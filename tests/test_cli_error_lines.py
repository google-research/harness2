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

"""A misconfiguration reaches the user as one `error:` line, not a traceback (#38)."""

import unittest

from harness2.cli import judge as judge_cli
from harness2.cli import report as report_cli


class CliErrorLineTests(unittest.TestCase):
    def test_report_rejects_an_unknown_mode_as_one_line(self):
        rc = report_cli.main(["--bench", "lab", "--mode", "bogus"])
        self.assertEqual(rc, 1)

    def test_report_rejects_a_subsetless_workbuddy_as_one_line(self):
        rc = report_cli.main(["--bench", "wb"])
        self.assertEqual(rc, 1)

    def test_judge_rejects_an_unknown_mode_as_one_line(self):
        rc = judge_cli.main(["--bench", "lab", "--mode", "bogus"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
