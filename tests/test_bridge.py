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

"""Offline CLI regressions for bridge port selection and endpoint persistence."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.cli import bridge


class BridgePortTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "litellm").touch()
        self.endpoint = self.root / "codex/bridge.json"
        self.enterContext(patch.object(bridge.sys, "executable", str(self.root / "python")))
        self.enterContext(patch.object(bridge.config, "DEPENDENCIES", self.root))
        self.enterContext(patch.object(bridge.config, "VERTEX_PROJECT", "offline-test-project"))
        self.enterContext(patch.dict(bridge.os.environ, {}, clear=True))
        self.launch = self.enterContext(patch.object(bridge.os, "execve"))
        self.free_port = self.enterContext(patch.object(bridge, "_free_port", return_value=54321))
        self.new_key = self.enterContext(
            patch.object(bridge.secrets, "token_urlsafe", return_value="offline-test-key")
        )
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_invalid_ports_do_not_write_or_launch(self):
        for saved in (False, True):
            if saved:
                self.endpoint.parent.mkdir(parents=True, exist_ok=True)
                self.endpoint.write_text('{"port": 4321, "key": "offline-test-key"}\n')
                original = self.endpoint.read_bytes()
            for port in ("-1", "65536"):
                with self.subTest(saved=saved, port=port):
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
                        bridge.main(["--port", port, "--model", "offline-test-model"])
                    self.assertEqual(error.exception.code, 2)
                    self.assertIn("--port must be between 0 and 65535", stderr.getvalue())
                    if saved:
                        self.assertEqual(self.endpoint.read_bytes(), original)
                    else:
                        self.assertFalse(self.endpoint.exists())
                    self.assertFalse((self.endpoint.parent / "bridge.yaml").exists())
                    self.launch.assert_not_called()
                    self.free_port.assert_not_called()
                    self.new_key.assert_not_called()

    def test_explicit_port_boundaries_are_forwarded(self):
        for port in (1, 65535):
            with self.subTest(port=port):
                bridge.main(["--port", str(port), "--model", "offline-test-model"])
                self.assertEqual(json.loads(self.endpoint.read_text())["port"], port)
                self.assertEqual(self.launch.call_args.args[1][-2:], ["--port", str(port)])
                self.free_port.assert_not_called()

    def test_zero_selects_free_port_then_reuses_saved_port(self):
        bridge.main(["--port", "0", "--model", "offline-test-model"])
        saved = json.loads(self.endpoint.read_text())
        self.assertEqual(saved["port"], 54321)
        self.free_port.assert_called_once_with("127.0.0.1")
        self.new_key.assert_called_once_with(24)
        for args in (["--port", "0"], []):
            with self.subTest(args=args):
                bridge.main([*args, "--model", "offline-test-model"])
                self.assertEqual(json.loads(self.endpoint.read_text()), saved)
                self.assertEqual(self.launch.call_args.args[1][-2:], ["--port", "54321"])
                self.free_port.assert_called_once()
                self.new_key.assert_called_once()


if __name__ == "__main__":
    unittest.main()
