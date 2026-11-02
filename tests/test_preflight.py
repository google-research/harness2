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

"""harness2-check reports a missing GNU timeout before a silent sweep failure."""

import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import preflight


class _Spec:
    name = "lab-cx"


class _Adapter:
    spec = _Spec()

    def tasks(self):
        return []

    def preflight(self):
        return None


class PreflightTimeoutTests(unittest.TestCase):
    def test_missing_timeout_is_reported_for_lab(self):
        adapter = _Adapter()
        with (
            patch.object(preflight, "missing_runners", return_value=None),
            patch.object(preflight.config, "validate_models", return_value=None),
            patch.object(
                preflight,
                "OpencodeSubstrate",
                return_value=type("S", (), {"preflight": lambda self: None})(),
            ),
            # Force the specific check under test.
            patch(
                "harness2.preflight.shutil.which",
                side_effect=lambda b: None if b == "timeout" else "/usr/bin/" + b,
            ),
            patch.object(preflight.config, "DEPENDENCIES", Path("/nonexistent")),
            patch.object(preflight.config, "DEPENDENCY_PINS", {}),
            patch.object(preflight.config, "THIRD_PARTY", Path("/nonexistent")),
            patch.object(preflight.config, "OPENCODE_DIR", Path("/nonexistent")),
            patch.object(preflight.config, "SOLVER_MODEL_OC", "opencode"),
            patch.object(preflight.config, "IMPROVER_MODEL", "opencode"),
            patch.object(preflight.config, "JUDGE_MODEL", "opencode"),
            patch.object(preflight.config, "LAB_PYTHON", "python"),
        ):
            errors = preflight.check(adapter)

        self.assertTrue(
            any("timeout" in e.lower() for e in errors),
            f"expected a missing-timeout error, got: {errors}",
        )


if __name__ == "__main__":
    unittest.main()
