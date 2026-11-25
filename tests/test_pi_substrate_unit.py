# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deterministic checks for the pi substrate interface (no model calls)."""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from harness2.substrate.base import Substrate
from harness2.substrate.pi import PiSubstrate


class PiSubstrateInterfaceTests(unittest.TestCase):
    def test_substrate_protocol(self) -> None:
        self.assertIsInstance(PiSubstrate(), Substrate)
        self.assertEqual(PiSubstrate().name, "pi")

    def test_preflight_requires_a_runnable_cli(self) -> None:
        substrate = PiSubstrate()
        substrate.preflight()  # this host has a working pi installation
        with unittest.mock.patch(
            "harness2.substrate.pi.pi_bin", return_value=Path("/definitely/not/pi")
        ):
            with self.assertRaises(RuntimeError):
                substrate.preflight()

    def test_read_trajectory_renders_and_truncates(self) -> None:
        events = [
            {
                "type": "tool_execution_start",
                "toolCallId": "t1",
                "toolName": "bash",
                "args": {"command": "python3 scripts/sum.py 19 23"},
            },
            {
                "type": "tool_execution_end",
                "toolCallId": "t1",
                "toolName": "bash",
                "isError": False,
                "result": {"content": "42"},
            },
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "42"}],
                },
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stream.jsonl"
            path.write_text("\n".join(json.dumps(event) for event in events))
            rendered = PiSubstrate().read_trajectory(
                path, obs_trunc=120, max_chars=1200, args_trunc=80
            )
            clipped = PiSubstrate().read_trajectory(
                path, obs_trunc=4, max_chars=30, args_trunc=4
            )
        self.assertIn("[ACTION] bash(", rendered)
        self.assertIn("scripts/sum.py", rendered)
        self.assertIn("[OBSERVATION] 42", rendered)
        self.assertIn("[ASSISTANT] 42", rendered)
        self.assertIn("…(+", clipped)
        self.assertTrue(clipped.endswith("render truncation)"))

if __name__ == "__main__":
    unittest.main()
