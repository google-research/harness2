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

"""Regressions for the OpenCode event renderer.

One malformed event used to raise out of `render_events`, which cost the task EVERY
rollout trajectory: the evidence tree could not be built, the stage fell back to the
base harness, and the task was still graded and still counted.
"""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.core.proposer import _opencode_stdout
from harness2.substrate.opencode import render_events

BAD_ERROR = {"type": "error", "error": {"name": "ProviderAuthError", "data": "boom"}}


class OpencodeEventTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def stream(self, *events: dict) -> str:
        return "\n".join(json.dumps(event) for event in events) + "\n"

    def render(self, *events: dict) -> str:
        path = self.root / "opencode_session.jsonl"
        path.write_text(self.stream(*events), encoding="utf-8")
        return render_events(path, obs_trunc=50_000, max_chars=10**9)

    def test_one_bad_event_does_not_cost_the_trajectory(self):
        rendered = self.render(
            {"type": "text", "part": {"type": "text", "text": "before"}},
            BAD_ERROR,
            {"type": "text", "part": {"type": "text", "text": "after"}},
        )
        self.assertNotIn("no parseable events", rendered)
        self.assertIn("before", rendered)
        self.assertIn("after", rendered)
        self.assertIn("[ERROR]", rendered)
        self.assertIn("ProviderAuthError", rendered)

    def test_the_renderer_agrees_with_the_proposer_parser(self):
        events = [{"type": "text", "part": {"type": "text", "text": "hello"}}, BAD_ERROR]
        text, error = _opencode_stdout(self.stream(*events))
        self.assertEqual(text, "hello")
        self.assertIn("ProviderAuthError", error)
        self.assertIn("[ERROR]", self.render(*events))

    def test_an_error_payload_that_is_not_an_object_is_not_dropped(self):
        rendered = self.render({"type": "error", "error": "session crashed"})
        self.assertIn("[ERROR] session crashed", rendered)

    def test_an_empty_error_object_is_still_reported(self):
        rendered = self.render({"type": "error", "error": {}})
        self.assertIn("[ERROR]", rendered)

    def test_a_part_without_a_type_falls_back_to_the_event_type(self):
        rendered = self.render(
            {"type": "text", "part": {"type": None, "text": "hello"}},
        )
        self.assertIn("[ASSISTANT] hello", rendered)

    def test_a_tool_invocation_that_is_not_an_object_is_rendered(self):
        rendered = self.render(
            {"type": "tool", "part": {"type": "tool", "toolInvocation": "bash"}}
        )
        self.assertIn("[ACTION]", rendered)


if __name__ == "__main__":
    unittest.main()
