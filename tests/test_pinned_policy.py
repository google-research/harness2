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

"""A deployer's pinned policy is staged, verified, fingerprinted, and charged to the cap."""

import dataclasses
import shutil
import tempfile
import unittest
from pathlib import Path

from harness2.core import policy, recursion, runmark, tree
from harness2.demo import DemoAdapter
from harness2.substrate.codex import CODEX_SURFACE, CodexSubstrate

POLICY = "Never delete files outside the workspace."


class PinnedPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.surface = self.adapter.substrate.surface()

    def stage(self, pinned: str) -> tuple[Path, str]:
        spec = dataclasses.replace(self.adapter.spec, pinned_policy=pinned)
        install = self.adapter.substrate.materialise(self.adapter.base_harness(), spec, {})
        self.addCleanup(shutil.rmtree, install.staging_dir, ignore_errors=True)
        return install.staging_dir, install.fingerprint

    def test_policy_stays_out_of_editable_and_staged_source(self):
        source = self.adapter.base_harness() / "systemprompt.md"
        before = source.read_text()
        staged, _ = self.stage(POLICY)
        text = (staged / "systemprompt.md").read_text()
        self.assertEqual(text, before)
        self.assertEqual(source.read_text(), before)

    def test_no_policy_changes_nothing(self):
        staged, fingerprint = self.stage("")
        source = self.adapter.base_harness()
        self.assertEqual(
            (staged / "systemprompt.md").read_text(), (source / "systemprompt.md").read_text()
        )
        self.assertEqual(fingerprint, runmark.fingerprint(source))
        self.assertEqual(policy.digest(""), "")
        self.assertEqual(policy.reserved_chars(""), 0)

    def test_policy_changes_the_fingerprint(self):
        _, none = self.stage("")
        _, one = self.stage(POLICY)
        _, two = self.stage(POLICY + " Ask before sending email.")
        self.assertEqual(len({none, one, two}), 3)

    def test_marker_from_another_policy_is_not_reusable(self):
        harness = self.adapter.base_harness()
        where = self.root / "mark"
        runmark.mark_complete(where, run_id="r", harness_dir=harness, salt=policy.digest(POLICY))
        self.assertTrue(runmark.is_complete(where, harness_dir=harness, salt=policy.digest(POLICY)))
        self.assertFalse(runmark.is_complete(where, harness_dir=harness))
        self.assertFalse(
            runmark.is_complete(where, harness_dir=harness, salt=policy.digest("other"))
        )

    def test_verify_fails_closed_on_tamper(self):
        staged, _ = self.stage(POLICY)
        target = staged / "systemprompt.md"
        policy.inject_file(target, POLICY, max_chars=self.surface.always_on_max)
        target.write_text(target.read_text().replace("Never delete", "Feel free to delete"))
        with self.assertRaisesRegex(ValueError, "pinned policy missing or altered"):
            policy.verify_file(target, POLICY, max_chars=self.surface.always_on_max)
        target.write_text("")
        with self.assertRaisesRegex(ValueError, "pinned policy missing or altered"):
            policy.verify_file(target, POLICY, max_chars=self.surface.always_on_max)

    def test_codex_delivers_policy_separately_from_staged_source(self):
        source = self.root / "cx"
        tree.write_tree(CODEX_SURFACE, {"AGENTS.md": "Do the work.\n"}, source, stubs=False)
        spec = dataclasses.replace(
            self.adapter.spec, install_mode="codex_root", pinned_policy=POLICY
        )
        install = CodexSubstrate().materialise(source, spec, {})
        self.addCleanup(shutil.rmtree, install.staging_dir, ignore_errors=True)
        self.assertEqual((install.staging_dir / "AGENTS.md").read_text(), "Do the work.\n")
        self.assertEqual(install.extras["pinned_policy"], POLICY)
        self.assertNotEqual(install.fingerprint, runmark.fingerprint(source))

    def test_policy_is_charged_to_the_always_on_cap(self):
        spec = dataclasses.replace(self.adapter.spec, pinned_policy=POLICY)
        adapter = self.adapter
        adapter.spec = spec
        proposed = {**tree.read_tree(self.surface, self.adapter.base_harness())}
        proposed["systemprompt.md"] = ""
        room = self.surface.always_on_max - len(tree.always_on_text(self.surface, proposed))
        body = "x" * (room - policy.reserved_chars(POLICY) + 1)
        proposed["systemprompt.md"] = body
        base = dict(proposed)
        _, err = recursion._accept_tree(self.surface, adapter, proposed, base, "t")
        self.assertIn("over the", err)
        proposed["systemprompt.md"] = body[:-1]
        _, err = recursion._accept_tree(self.surface, adapter, proposed, base, "t")
        self.assertNotIn("over the", err)


if __name__ == "__main__":
    unittest.main()
