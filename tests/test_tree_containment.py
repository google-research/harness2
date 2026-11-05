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

"""Regressions for editable-tree and link containment."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.core import context, proposer, recursion, runmark, tree
from harness2.core.proposer import Proposer
from harness2.demo import DemoAdapter, DemoProposer


class TreeContainmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.proposer = DemoProposer(self.adapter.spec.channels["proposer"])

    def test_symlink_and_surface_containment(self):
        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        self.assertFalse(tree.is_allowed(surface, "scripts/bad\x00.py"))
        self.assertFalse(tree.is_allowed(surface, "..\\outside.py"))
        with self.assertRaises(ValueError):
            tree.write_tree(surface, {**base, "off_surface.txt": "bad"}, self.root / "bad_dest")

        outside = self.root / "outside.py"
        outside.write_text("secret = 1\n")
        staged = tree.write_tree(surface, base, self.root / "staged")
        orig_prompt = (staged / "systemprompt.md").read_text()
        with self.assertRaises(ValueError):
            tree.write_tree(surface, {"systemprompt.md": "mutated\n", "../evil.md": "x"}, staged)
        self.assertEqual((staged / "systemprompt.md").read_text(), orig_prompt)
        (staged / "scripts").mkdir(exist_ok=True)
        (staged / "scripts/escape.py").symlink_to(outside)
        (staged / "scripts/dangling.py").symlink_to(self.root / "missing.py")
        rejected = []
        loaded = tree.read_tree(surface, staged, rejected=rejected)
        self.assertEqual(sorted(rejected), ["scripts/dangling.py", "scripts/escape.py"])
        self.assertNotIn("scripts/escape.py", loaded)

        (staged / "guardrails.md").unlink()
        (staged / "guardrails.md").symlink_to(outside)
        tree.write_tree(surface, base, staged)
        self.assertFalse((staged / "guardrails.md").is_symlink())
        self.assertEqual(outside.read_text(), "secret = 1\n")

        sym_dir, empty_dir = self.root / "sym_harness", self.root / "empty_dir"
        sym_dir.mkdir()
        empty_dir.mkdir()
        (sym_dir / "systemprompt.md").symlink_to(outside)
        self.assertFalse(tree.is_tree(surface, sym_dir))
        linked = runmark.fingerprint(sym_dir)
        self.assertNotEqual(linked, runmark.fingerprint(empty_dir))
        outside.write_text("secret = 2\n")
        self.assertNotEqual(runmark.fingerprint(sym_dir), linked)
        outside.write_text("secret = 1\n")
        linked = runmark.fingerprint(sym_dir)
        (sym_dir / "shared").symlink_to(empty_dir)
        self.assertEqual(runmark.fingerprint(sym_dir), linked)

        sym_root = self.root / "sym_root_link"
        sym_root.symlink_to(staged)
        self.assertEqual(tree.read_tree(surface, sym_root), {})
        self.assertFalse(tree.is_tree(surface, sym_root))
        self.assertEqual(runmark.fingerprint(sym_root), runmark.fingerprint(staged))
        with self.assertRaises(ValueError):
            tree.write_tree(surface, base, sym_root)

        prop_inst = Proposer(self.proposer.spec, audit_dir=self.root / "audit")
        (staged / "scripts").mkdir(exist_ok=True)
        (staged / "scripts/escape.py").symlink_to(outside)
        harvested = prop_inst._harvest(self.root, "staged", "h1")
        self.assertTrue((harvested / "scripts/escape.py").is_symlink())
        self.assertIsNone(prop_inst._harvest(self.root, "sym_root_link", "h2"))

    def test_hard_linked_copies_are_read(self):
        """An operator's hard-linked copy (`cp -al`, `rsync --link-dest`) is still a tree."""
        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        staged = tree.write_tree(surface, base, self.root / "staged")
        snap = self.root / "snap"
        for p in staged.rglob("*"):
            if p.is_file():
                (snap / p.relative_to(staged)).parent.mkdir(parents=True, exist_ok=True)
                os.link(p, snap / p.relative_to(staged))
        rejected = []
        self.assertTrue(tree.is_tree(surface, snap))
        self.assertEqual(tree.read_tree(surface, snap, rejected=rejected), base)
        self.assertEqual(rejected, [])

    def test_linked_stub_is_skipped_not_rejected(self):
        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        outside = self.root / "outside.md"
        outside.write_text("secret\n")
        staged = tree.write_tree(surface, base, self.root / "staged")
        stub = staged / surface.stubs[0][0]
        stub.unlink()
        stub.symlink_to(outside)
        rejected = []
        self.assertEqual(tree.read_tree(surface, staged, rejected=rejected), base)
        self.assertEqual(rejected, [])

    def test_harvested_links_are_rejected(self):
        """Through `_harvest`, as a proposal comes back: neither kind of link reaches the tree."""
        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        outside = self.root / "outside.md"
        outside.write_text("secret\n")
        session = self.root / "session"
        staged = tree.write_tree(surface, base, session / "context/harness")
        (staged / "systemprompt.md").unlink()
        (staged / "systemprompt.md").symlink_to(outside)
        (staged / "guardrails.md").unlink()
        os.link(outside, staged / "guardrails.md")
        prop = Proposer(self.proposer.spec, audit_dir=self.root / "audit")
        harvested = prop._harvest(session, "context/harness", "h3")
        rejected = []
        loaded = tree.read_tree(surface, harvested, rejected=rejected)
        self.assertEqual(sorted(rejected), ["guardrails.md", "systemprompt.md"])
        self.assertNotIn("secret\n", loaded.values())
        err = recursion._rejected_error(surface, harvested, rejected)
        self.assertIn("these are links, not files, and were not applied: guardrails.md, ", err)

    def test_harvest_stays_in_the_session(self):
        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        prop = Proposer(self.proposer.spec, audit_dir=self.root / "audit")

        elsewhere = self.root / "elsewhere"
        tree.write_tree(surface, base, elsewhere / "harness")
        session = self.root / "session_linked"
        session.mkdir()
        (session / "context").symlink_to(elsewhere)
        self.assertIsNone(prop._harvest(session, "context/harness", "h4"))

        session = self.root / "session_fifo"
        staged = tree.write_tree(surface, base, session / "context/harness")
        os.mkfifo(staged / "scripts/pipe.py")
        harvested = prop._harvest(session, "context/harness", "h5")
        self.assertFalse((harvested / "scripts/pipe.py").exists())
        self.assertEqual(tree.read_tree(surface, harvested), base)

    def test_special_files_are_skipped_not_rejected(self):
        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        staged = tree.write_tree(surface, base, self.root / "staged")
        os.mkfifo(staged / "scripts/pipe.py")
        rejected = []
        loaded = tree.read_tree(surface, staged, rejected=rejected)
        self.assertEqual(rejected, [])
        self.assertEqual(loaded, base)

    def test_all_links_get_the_link_retry_message(self):
        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        outside = self.root / "outside.md"
        outside.write_text("secret\n")
        linked = self.root / "linked"
        for rel in base:
            (linked / rel).parent.mkdir(parents=True, exist_ok=True)
            (linked / rel).symlink_to(outside)

        class LinkingProposer:
            def __init__(self):
                self.missions = []

            def call(self, mission, *args, **kwargs):
                self.missions.append(mission)
                return proposer.Reply(label="links", text="", ok=True, workspace=linked)

        prop = LinkingProposer()
        recursion._run_stage(
            label="links",
            dim="INV",
            job="Edit the harness",
            fallback=base,
            adapter=self.adapter,
            prop=prop,
            ctx_dir=self.root / "ctx",
            task_id="ledger-1",
            rollouts=[],
            harness_dir=self.adapter.base_harness(),
            library_dir=None,
        )
        self.assertGreater(len(prop.missions), 1)
        self.assertIn("these are links, not files", prop.missions[1])
        self.assertNotIn("came back empty", prop.missions[1])

    def test_links_get_their_own_retry_message(self):
        surface = self.adapter.substrate.surface()
        outside = self.root / "outside.md"
        outside.write_text("secret\n")
        ws = self.root / "ws"
        ws.mkdir()
        (ws / "systemprompt.md").symlink_to(outside)
        (ws / "guardrails.md").symlink_to(outside)
        (ws / "Bad_Name.md").write_text("x\n")
        rejected = []
        tree.read_tree(surface, ws, rejected=rejected)
        err = recursion._rejected_error(surface, ws, rejected)
        self.assertIn("these are links, not files, and were not applied: guardrails.md, ", err)
        self.assertIn("systemprompt.md. Replace each with real files", err)
        self.assertIn("off the editable surface and were not applied: Bad_Name.md.", err)
        only_off = recursion._rejected_error(surface, ws, ["Bad_Name.md"])
        self.assertNotIn("links", only_off)

    def test_linked_answer_is_not_read(self):
        outside = self.root / "outside.md"
        outside.write_text("secret answer\n")
        for name, link in (("symlink", Path.symlink_to), ("hard link", Path.hardlink_to)):
            with self.subTest(name):
                cwd = self.root / f"session_{name.replace(' ', '_')}"
                cwd.mkdir()

                def fake_run(*args, session=cwd, make=link, **kwargs):
                    make(session / "output" / "answer.md", outside)
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

                prop = Proposer(self.proposer.spec)
                with (
                    patch.object(proposer, "_opencode_dir", return_value=self.root),
                    patch.object(proposer.subprocess, "run", side_effect=fake_run),
                    patch.object(Proposer, "env", return_value={}),
                ):
                    text, _, _ = prop._opencode(cwd, "mission", "", 10)
                self.assertNotIn("secret", text)

    def test_off_surface_tree_is_a_failed_rollout(self):
        """A tree that cannot be written is recorded as a dead rollout, not raised."""
        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        ledger = recursion.Ledger(mode="agg", task_id="t", k=1)
        rv = recursion._roll(
            self.adapter,
            ledger,
            "t",
            "bad",
            {**base, "off_surface.txt": "x"},
            lambda label: label,
            self.root / "work",
        )
        self.assertFalse(rv.ok)
        self.assertIn("off the editable surface", rv.error)
        self.assertEqual([e.label for e in ledger.entries], ["bad"])

    def test_linked_tree_is_not_a_tree(self):
        """A symlink anywhere under a harness keeps it from being staged for an agent."""
        surface = self.adapter.substrate.surface()
        base = tree.read_tree(surface, self.adapter.base_harness())
        staged = tree.write_tree(surface, base, self.root / "staged")
        self.assertTrue(tree.is_tree(surface, staged))
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret = 1\n")
        (staged / "assets").symlink_to(outside)
        self.assertFalse(tree.is_tree(surface, staged))

    def test_context_copy_skips_links(self):
        """A link in a harness copied into proposer context is not followed."""
        src = self.root / "src"
        (src / "scripts").mkdir(parents=True)
        (src / "scripts/ok.py").write_text("ok = 1\n")
        outside = self.root / "outside.txt"
        outside.write_text("secret = 1\n")
        (src / "scripts/escape.py").symlink_to(outside)
        dst = self.root / "dst"
        self.assertEqual(context._copy_tree(src, dst), 1)
        self.assertFalse((dst / "scripts/escape.py").exists())
        self.assertTrue((dst / "scripts/ok.py").is_file())


if __name__ == "__main__":
    unittest.main()
