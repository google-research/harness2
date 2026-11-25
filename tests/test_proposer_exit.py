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

"""Failed improver processes must not commit partial replies to the audit cache."""

import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core.proposer import Proposer, ProposerError, _call_fingerprint
from harness2.core.spec import ChannelSpec


@unittest.skipUnless(os.name == "posix", "the supported executor platform is Linux")
class ProposerExitTests(unittest.TestCase):
    @contextlib.contextmanager
    def run_session(self, channel, codes, *, answer_file=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "codes.json"
            state.write_text(json.dumps(list(codes)))
            executable = root / "protocol-session"
            events = (
                {"result": "partial proposal", "total_cost_usd": 0.25}
                if channel == "claude_cli"
                else {"type": "text", "part": {"text": "partial proposal"}}
            )
            script = "#!/usr/bin/env python3\nimport json,pathlib,sys\n"
            script += f"state = pathlib.Path({str(state)!r})\n"
            script += "codes = json.loads(state.read_text())\ncode = codes.pop(0)\n"
            script += "state.write_text(json.dumps(codes))\n"
            if answer_file:
                script += "pathlib.Path('output/answer.md').write_text('partial proposal'); "
            script += f"print({json.dumps(json.dumps(events))}); "
            if channel == "opencode":
                script += 'print(\'{"type":"step_finish","part":{"cost":0.25}}\'); '
            script += "sys.exit(code)"
            executable.write_text(script)
            executable.chmod(0o700)
            with (
                patch("harness2.core.proposer.shutil.which", return_value=str(executable)),
                patch("harness2.config.BUN", executable),
            ):
                yield

    def test_nonzero_reply_is_not_accepted_or_cached(self):
        for channel, answer_file in [
            ("claude_cli", False),
            ("opencode", False),
            ("opencode", True),
        ]:
            with (
                self.subTest(channel=channel, answer_file=answer_file),
                tempfile.TemporaryDirectory() as td,
            ):
                prop = Proposer(ChannelSpec(channel, "test-model"), audit_dir=Path(td), attempts=1)
                with (
                    self.run_session(channel, iter([7]), answer_file=answer_file),
                    patch("harness2.core.proposer._opencode_dir", return_value=Path(td)),
                ):
                    with self.assertRaises(ProposerError) as caught:
                        prop.call("improve the harness", None, "inv1")
                self.assertEqual(caught.exception.cost_usd, 0.25)
                self.assertIn("7", str(caught.exception))
                self.assertFalse(prop._calls[-1].ok)
                key = _call_fingerprint("inv1", "improve the harness", "", None)
                self.assertIsNone(prop._cached("inv1", key))
                meta = json.loads(prop._paths("inv1", key)["meta"].read_text())
                self.assertFalse(meta["ok"])
                self.assertEqual(meta["cost_usd"], 0.25)

    def test_zero_exit_still_accepts_reply(self):
        for channel in ("claude_cli", "opencode"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as td:
                prop = Proposer(ChannelSpec(channel, "test-model"), attempts=1)
                with (
                    self.run_session(channel, iter([0])),
                    patch("harness2.core.proposer._opencode_dir", return_value=Path(td)),
                ):
                    reply = prop.call("improve", None, "inv1")
                self.assertTrue(reply.ok)
                self.assertEqual(reply.text, "partial proposal")
                self.assertEqual(reply.cost_usd, 0.25)

    def test_retry_retains_failed_attempt_spend(self):
        for channel in ("claude_cli", "opencode"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as td:
                prop = Proposer(ChannelSpec(channel, "test-model"), attempts=2)
                with (
                    self.run_session(channel, iter([7, 0])),
                    patch("harness2.core.proposer._opencode_dir", return_value=Path(td)),
                    patch("harness2.core.proposer.time.sleep"),
                ):
                    reply = prop.call("improve", None, "inv1")
                self.assertTrue(reply.ok)
                self.assertEqual(reply.attempts, 2)
                self.assertEqual(reply.cost_usd, 0.5)
