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

"""A missing GNU `timeout` is named by the preflight, not discovered as a dead rollout (#77)."""

import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import preflight


class _Spec:
    name = "jb-oc"


class _Adapter:
    spec = _Spec()

    def tasks(self, split="all"):
        return []

    def preflight(self):
        return None


class PreflightTimeoutTests(unittest.TestCase):
    def test_missing_timeout_is_reported_for_opencode_benchmarks(self):
        with (
            patch.object(preflight, "missing_runners", return_value=""),
            patch.object(
                preflight.shutil, "which", side_effect=lambda b: None if b == "timeout" else b
            ),
            patch.object(preflight.config, "validate_models"),
        ):
            errors = preflight.check(_Adapter())
        self.assertTrue(any("GNU timeout missing" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
