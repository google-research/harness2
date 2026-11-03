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

"""`harness2-setup --dry-run` previews the Codex setup before its endpoint is configured."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config, install
from harness2.cli import setup


class SetupDryRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "codex" / "home"
        for target in (
            patch.dict(os.environ, {"HARNESS2_CODEX_BASE_URL": ""}),
            patch.object(config, "CODEX_BASE_URL", ""),
            patch.object(config, "DEPENDENCIES", Path(temporary.name)),
            patch.object(config, "CODEX_HOME_DIR", self.home),
        ):
            target.start()
            self.addCleanup(target.stop)

    def test_dry_run_without_codex_endpoint_prints_the_plan(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = setup.main(["jb", "--substrate", "cx", "--dry-run"])
        self.assertEqual(status, 0)
        self.assertIn("HARNESS2_CODEX_BASE_URL is required", out.getvalue())
        self.assertIn("Setup plan complete.", out.getvalue())
        self.assertFalse(self.home.exists())

    def test_real_setup_still_requires_codex_endpoint(self):
        with self.assertRaisesRegex(config.ConfigError, "HARNESS2_CODEX_BASE_URL"):
            install.configure_codex()
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
