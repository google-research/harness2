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

"""Proposer cache isolation using real audit files and scripted model transports."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from harness2.core.proposer import Proposer
from harness2.core.spec import ChannelSpec


class ProposerCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.audit = self.root / "audit"
        self.spec = ChannelSpec("opencode", "provider/model-a")
        self.context = self.root / "context"
        (self.context / "harness").mkdir(parents=True)
        (self.context / "harness" / "script.py").write_text("print('base')\n", encoding="utf-8")

    def test_cache_is_scoped_to_channel_configuration(self):
        specs = (
            self.spec,
            replace(self.spec, model="provider/model-b"),
            replace(self.spec, kind="claude_cli"),
            replace(self.spec, effort="low"),
            replace(self.spec, steps=10),
        )
        for index, spec in enumerate(specs):
            with self.subTest(spec=spec):
                expected = f"reply {index}"
                for cached in (False, True):
                    # A new instance must still reuse a matching on-disk cache entry.
                    proposer = Proposer(spec, audit_dir=self.audit)
                    transport = "_claude_cli" if spec.kind == "claude_cli" else "_opencode"
                    with patch.object(
                        proposer, transport, return_value=(expected, 0.25, "")
                    ) as call:
                        reply = proposer.call("Edit the harness", self.context, "inv1.a1")
                    self.assertEqual(reply.text, expected)
                    self.assertEqual(reply.cached, cached)
                    self.assertEqual(reply.cost_usd, 0 if cached else 0.25)
                    self.assertEqual(call.call_count, 0 if cached else 1)

        proposer = Proposer(self.spec, audit_dir=self.audit)
        with patch.object(proposer, "_opencode") as call:
            reply = proposer.call("Edit the harness", self.context, "inv1.a1")
        call.assert_not_called()
        self.assertTrue(reply.cached)
        self.assertEqual(reply.text, "reply 0")

    def test_timeout_only_change_reuses_cached_reply(self):
        """A timeout decides whether a call finishes, not what it says: a reply that
        completed under one timeout is still valid under another."""
        proposer = Proposer(self.spec, audit_dir=self.audit)
        with patch.object(proposer, "_opencode", return_value=("reply", 0.25, "")) as call:
            reply = proposer.call("Edit the harness", self.context, "inv1.a1")
        call.assert_called_once()
        self.assertFalse(reply.cached)

        proposer = Proposer(replace(self.spec, timeout=7200), audit_dir=self.audit)
        with patch.object(proposer, "_opencode", return_value=("other", 0.25, "")) as call:
            reply = proposer.call("Edit the harness", self.context, "inv1.a1")
        call.assert_not_called()
        self.assertTrue(reply.cached)
        self.assertEqual(reply.text, "reply")

    def test_changed_model_does_not_reuse_harvested_harness(self):
        workspaces = []
        for model in ("provider/model-a", "provider/model-b"):
            with self.subTest(model=model):
                expected = f"print({model!r})\n"

                def edit(cwd, mission, system, wall):
                    (cwd / "context/harness/script.py").write_text(expected, encoding="utf-8")
                    return "Edited the script", 0.25, ""

                proposer = Proposer(replace(self.spec, model=model), audit_dir=self.audit)
                with patch.object(proposer, "_opencode", side_effect=edit) as call:
                    reply = proposer.call(
                        "Edit the harness", self.context, "inv1.a1", harvest="context/harness"
                    )
                call.assert_called_once()
                self.assertFalse(reply.cached)
                self.assertEqual((reply.workspace / "script.py").read_text(), expected)
                workspaces.append(reply.workspace)

                with patch.object(proposer, "_opencode") as call:
                    cached = proposer.call(
                        "Edit the harness", self.context, "inv1.a1", harvest="context/harness"
                    )
                call.assert_not_called()
                self.assertTrue(cached.cached)
                self.assertEqual(cached.workspace, reply.workspace)
                self.assertEqual((cached.workspace / "script.py").read_text(), expected)

        self.assertEqual(len(workspaces), 2)
        self.assertNotEqual(workspaces[0], workspaces[1])
        self.assertEqual((self.context / "harness/script.py").read_text(), "print('base')\n")


if __name__ == "__main__":
    unittest.main()
