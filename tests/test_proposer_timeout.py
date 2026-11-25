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

"""Timed-out improver sessions must stop their running descendants."""

import os
import signal
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core.proposer import Proposer, ProposerError
from harness2.core.spec import ChannelSpec


@unittest.skipUnless(os.name == "posix", "process groups require POSIX")
class ProposerTimeoutTests(unittest.TestCase):
    def test_successful_session_receives_stdin(self):
        with tempfile.TemporaryDirectory() as td:
            executable = Path(td) / "session"
            executable.write_text(
                "#!/usr/bin/env python3\nimport json,sys\n"
                "print(json.dumps({'result': sys.stdin.read(), 'total_cost_usd': 0.25}))\n"
            )
            executable.chmod(0o700)
            prop = Proposer(ChannelSpec("claude_cli", "local/process-fixture"), attempts=1)
            with patch("harness2.core.proposer.shutil.which", return_value=str(executable)):
                reply = prop.call("complete input mission", None, "inv1", timeout=2)
            self.assertEqual(reply.text, "complete input mission")
            self.assertEqual(reply.cost_usd, 0.25)

    def test_timeout_stops_descendant_work(self):
        for channel in ("opencode", "claude_cli"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                pid_path, survived = root / "child.pid", root / "survived"
                child = (
                    "import pathlib,time; time.sleep(2); "
                    f"pathlib.Path({str(survived)!r}).write_text('continued after deadline'); "
                    "time.sleep(60)"
                )
                executable = root / "session"
                executable.write_text(
                    "#!/usr/bin/env python3\nimport pathlib,subprocess,sys,time\n"
                    f"p = subprocess.Popen([sys.executable, '-c', {child!r}], "
                    "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                    f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid))\n"
                    "time.sleep(60)\n"
                )
                executable.chmod(0o700)
                prop = Proposer(ChannelSpec(channel, "local/process-fixture"), attempts=1)
                try:
                    with (
                        patch("harness2.config.BUN", executable),
                        patch("harness2.core.proposer.shutil.which", return_value=str(executable)),
                        patch("harness2.core.proposer._opencode_dir", return_value=root),
                    ):
                        with self.assertRaises(ProposerError) as caught:
                            prop.call("run the offline fixture", None, "inv1", timeout=1)
                    self.assertIn("timeout", str(caught.exception))
                    self.assertTrue(pid_path.is_file(), "the session must spawn a real descendant")
                    time.sleep(2)
                    self.assertFalse(survived.exists(), "descendant continued work after timeout")
                finally:
                    if pid_path.is_file():
                        try:
                            os.kill(int(pid_path.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
