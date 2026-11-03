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

"""Proposer caches must contain a complete reply and any harvested workspace."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core.proposer import Proposer, ProposerError
from harness2.core.spec import ChannelSpec


class InterruptedAudit(BaseException):
    """Simulate process interruption without entering the normal retry path."""


class ProposerCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spec = ChannelSpec(kind="opencode", model="offline-test")
        self.text = "<selection>agg1</selection>\n"
        transport = patch.object(Proposer, "_opencode", side_effect=self.respond)
        self.transport = transport.start()
        self.addCleanup(transport.stop)

    def respond(self, cwd, mission, system, wall):
        workspace = cwd / "edited"
        (workspace / "empty").mkdir(parents=True)
        (workspace / "runner.py").write_text("print('complete')\n", encoding="utf-8")
        (workspace / "data.json").write_text('{"complete": true}\n', encoding="utf-8")
        return self.text, 0.25, ""

    def call(self, label="proposal", harvest=None):
        # A fresh instance models resuming in another process.
        return Proposer(self.spec, audit_dir=self.root, attempts=1).call(
            "Improve the harness", None, label, harvest=harvest
        )

    def metadata_path(self, reply):
        return reply.audit.with_name(reply.audit.name.removesuffix(".response.md") + ".meta.json")

    def test_complete_reply_is_reused_without_new_cost(self):
        first = self.call()
        cached = self.call()
        self.assertFalse(first.cached)
        self.assertEqual(first.cost_usd, 0.25)
        self.assertTrue(cached.cached)
        self.assertTrue(cached.ok)
        self.assertEqual(cached.text, self.text)
        self.assertEqual(cached.cost_usd, 0.0)
        self.assertEqual(cached.audit, first.audit)
        self.assertIsNone(cached.workspace)
        self.assertEqual(self.transport.call_count, 1)

    def test_interruption_before_metadata_requires_another_session(self):
        with patch("harness2.core.proposer.json.dumps", side_effect=InterruptedAudit):
            with self.assertRaises(InterruptedAudit):
                self.call()
        self.assertTrue(list(self.root.glob("*.response.md")))
        self.assertFalse(list(self.root.glob("*.meta.json")))
        resumed = self.call()
        self.assertFalse(resumed.cached)
        self.assertEqual(resumed.text, self.text)
        self.assertEqual(self.transport.call_count, 2)

    def test_interrupted_publication_leaves_no_complete_entry(self):
        replace = os.replace
        for suffix in ("response.md", "meta.json"):
            with self.subTest(suffix=suffix):
                first = self.call(suffix)
                first.audit.write_text("damaged")

                def interrupted_replace(src, dst):
                    if Path(dst).name.endswith(suffix):
                        raise InterruptedAudit
                    return replace(src, dst)

                with patch("harness2.core.proposer.os.replace", side_effect=interrupted_replace):
                    with self.assertRaises(InterruptedAudit):
                        self.call(suffix)
                self.assertFalse(self.metadata_path(first).exists())
                self.assertFalse(list(self.root.glob(".*")))
                if suffix == "response.md":
                    self.assertEqual(first.audit.read_text(), "damaged")
                calls = self.transport.call_count
                resumed = self.call(suffix)
                self.assertFalse(resumed.cached)
                self.assertEqual(resumed.text, self.text)
                self.assertEqual(self.transport.call_count, calls + 1)

    def test_missing_invalid_or_legacy_metadata_is_a_cache_miss(self):
        for damage in ("missing", "truncated", "wrong-type", "failed", "legacy", "wrong-hash"):
            with self.subTest(damage=damage):
                first = self.call(damage)
                meta_path = self.metadata_path(first)
                meta = json.loads(meta_path.read_text())
                if damage == "missing":
                    meta_path.unlink()
                elif damage == "truncated":
                    meta_path.write_text('{"ok":')
                elif damage == "wrong-type":
                    meta_path.write_text("[]")
                else:
                    if damage == "failed":
                        meta["ok"] = False
                    elif damage == "legacy":
                        meta.pop("response_sha256", None)
                        meta.pop("workspace_sha256", None)
                    else:
                        meta["response_sha256"] = "incorrect"
                    meta_path.write_text(json.dumps(meta))
                calls = self.transport.call_count
                resumed = self.call(damage)
                self.assertFalse(resumed.cached)
                self.assertEqual(resumed.text, self.text)
                self.assertEqual(self.transport.call_count, calls + 1)
                self.assertTrue(self.call(damage).cached)

    def test_damaged_response_is_a_cache_miss(self):
        for damage in (b"<selection>", b"", b"\xff", None):
            with self.subTest(damage=damage):
                label = f"response-{self.transport.call_count}"
                first = self.call(label)
                if damage is None:
                    first.audit.unlink()
                else:
                    first.audit.write_bytes(damage)
                calls = self.transport.call_count
                resumed = self.call(label)
                self.assertFalse(resumed.cached)
                self.assertEqual(resumed.text, self.text)
                self.assertEqual(self.transport.call_count, calls + 1)

    def test_complete_harvest_is_preserved_on_cache_hit(self):
        first = self.call(harvest="edited")
        cached = self.call(harvest="edited")
        self.assertTrue(cached.cached)
        self.assertEqual(cached.workspace, first.workspace)
        self.assertEqual((cached.workspace / "runner.py").read_text(), "print('complete')\n")
        self.assertTrue((cached.workspace / "empty").is_dir())
        self.assertEqual(self.transport.call_count, 1)

    def test_damaged_harvest_is_a_cache_miss(self):
        for damage in ("missing", "changed-file", "missing-file", "extra-file", "missing-directory"):
            with self.subTest(damage=damage):
                first = self.call(damage, harvest="edited")
                if damage == "missing":
                    shutil.rmtree(first.workspace)
                elif damage == "changed-file":
                    (first.workspace / "runner.py").write_text("partial")
                elif damage == "missing-file":
                    (first.workspace / "data.json").unlink()
                elif damage == "extra-file":
                    (first.workspace / "unexpected.py").write_text("extra")
                else:
                    (first.workspace / "empty").rmdir()
                calls = self.transport.call_count
                resumed = self.call(damage, harvest="edited")
                self.assertFalse(resumed.cached)
                self.assertEqual(self.transport.call_count, calls + 1)
                self.assertEqual((resumed.workspace / "runner.py").read_text(), "print('complete')\n")
                self.assertTrue((resumed.workspace / "empty").is_dir())
                self.assertTrue(self.call(damage, harvest="edited").cached)

    def test_interrupted_harvest_cannot_reuse_previous_reply(self):
        first = self.call(harvest="edited")
        shutil.rmtree(first.workspace)
        copytree = shutil.copytree

        def interrupted_copy(src, dst, *args, **kwargs):
            if Path(dst).suffix == ".workspace":
                Path(dst).mkdir()
                (Path(dst) / "runner.py").write_text("partial")
                raise InterruptedAudit
            return copytree(src, dst, *args, **kwargs)

        with patch("harness2.core.proposer.shutil.copytree", side_effect=interrupted_copy):
            with self.assertRaises(InterruptedAudit):
                self.call(harvest="edited")
        resumed = self.call(harvest="edited")
        self.assertFalse(resumed.cached)
        self.assertEqual(self.transport.call_count, 3)
        self.assertTrue((resumed.workspace / "data.json").is_file())
        self.assertEqual((resumed.workspace / "runner.py").read_text(), "print('complete')\n")

    def test_unrecorded_workspace_does_not_satisfy_harvest_request(self):
        first = self.call()
        stale = first.audit.with_name(first.audit.name.removesuffix(".response.md") + ".workspace")
        stale.mkdir()
        (stale / "runner.py").write_text("stale")
        cached = self.call()
        self.assertTrue(cached.cached)
        self.assertIsNone(cached.workspace)
        harvested = self.call(harvest="edited")
        self.assertFalse(harvested.cached)
        self.assertEqual((harvested.workspace / "runner.py").read_text(), "print('complete')\n")
        self.assertEqual(self.transport.call_count, 2)

    def test_failed_reply_is_not_reused(self):
        self.transport.side_effect = None
        self.transport.return_value = "", 0.25, "model failed"
        with self.assertRaises(ProposerError):
            self.call()
        self.transport.side_effect = self.respond
        resumed = self.call()
        self.assertFalse(resumed.cached)
        self.assertEqual(resumed.text, self.text)
        self.assertEqual(self.transport.call_count, 2)

    def test_calls_without_audit_directory_are_not_cached(self):
        proposer = Proposer(self.spec, attempts=1)
        for _ in range(2):
            reply = proposer.call("Improve the harness", None, "proposal")
            self.assertFalse(reply.cached)
            self.assertIsNone(reply.audit)
        self.assertEqual(self.transport.call_count, 2)


if __name__ == "__main__":
    unittest.main()
