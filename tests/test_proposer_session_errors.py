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

"""Pinned OpenCode JSONL protocol stand-ins; no executor or model is contacted."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.core.proposer import Proposer, ProposerError
from harness2.core.spec import ChannelSpec


class ProposerSessionErrorsTests(unittest.TestCase):
    def test_session_error_rejects_partial_output_and_preserves_cost(self):
        for answer_file in (False, True):
            with self.subTest(answer_file=answer_file):
                self.run_session(answer_file=answer_file, fail=True)

    def test_successful_file_and_stdout_answers_remain_usable(self):
        for answer_file in (False, True):
            with self.subTest(answer_file=answer_file):
                self.run_session(answer_file=answer_file, fail=False)

    def run_session(self, *, answer_file, fail):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = root / "opencode/packages/opencode/src/index.ts"
            entry.parent.mkdir(parents=True)
            entry.write_text("")
            events = [
                {"type": "text", "part": {"type": "text", "text": "partial or final answer"}},
                {"type": "step_finish", "part": {"type": "step-finish", "cost": 0.25}},
            ]
            if fail:
                events.append(
                    {
                        "type": "error",
                        "error": {"name": "APIError", "data": {"message": "provider unavailable"}},
                    }
                )
            executable = root / "protocol-standin"
            executable.write_text(
                "#!/usr/bin/env python3\nfrom pathlib import Path\n"
                + (
                    "Path('output/answer.md').write_text('partial or final answer')\n"
                    if answer_file
                    else ""
                )
                + "print("
                + repr("\n".join(json.dumps(e) for e in events))
                + ")\n"
            )
            executable.chmod(0o700)
            audit = root / "audit"
            proposer = Proposer(
                ChannelSpec("opencode", "local/protocol-fixture"), audit_dir=audit, attempts=1
            )
            with (
                patch.object(config, "BUN", executable),
                patch.object(config, "OPENCODE_DIR", root / "opencode"),
            ):
                if fail:
                    with self.assertRaises(ProposerError) as caught:
                        proposer.call("Read the local protocol fixture", None, "fixture")
                    self.assertIn("provider unavailable", str(caught.exception))
                    self.assertEqual(caught.exception.cost_usd, 0.25)
                    self.assertFalse(proposer._calls[-1].ok)
                    self.assertEqual(proposer._calls[-1].text, "")
                    self.assertFalse(
                        any(p.read_text().strip() for p in audit.glob("*.response.md"))
                    )
                else:
                    reply = proposer.call("Read the local protocol fixture", None, "fixture")
                    self.assertTrue(reply.ok)
                    self.assertEqual(reply.text, "partial or final answer")
                    self.assertEqual(reply.cost_usd, 0.25)
