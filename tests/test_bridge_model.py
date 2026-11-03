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

"""`harness2-bridge` refuses to start without a model to serve."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.cli import bridge


class BridgeModelTests(unittest.TestCase):
    def test_empty_model_exits_before_writing_config_or_starting(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(config, "DEPENDENCIES", Path(tmp)),
            patch.object(config, "VERTEX_PROJECT", "project"),
            patch.object(Path, "is_file", return_value=True),
            patch.object(bridge.os, "execve") as execve,
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            with self.assertRaises(SystemExit) as exit_:
                bridge.main(["--model", ""])
            self.assertFalse((Path(tmp) / "codex").exists())
        self.assertEqual(exit_.exception.code, 1)
        self.assertIn("HARNESS2_CODEX_MODEL", err.getvalue())
        execve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
