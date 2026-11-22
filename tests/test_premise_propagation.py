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

"""Tests for --blind-compose and prohibition replay (E.6 premise propagation, #199)."""

import tempfile
import unittest
from pathlib import Path

from harness2.core import prohibition, recursion
from harness2.core.proposer import Reply
from harness2.core.spec import RunView
from harness2.demo import DemoAdapter, DemoProposer


class PremisePropagationTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)

    def test_extract_and_replay_prohibitions(self):
        base_tree = {
            "guardrails.md": "Always inspect source files before editing.\n",
        }
        cand_tree = {
            "guardrails.md": (
                "Always inspect source files before editing.\n"
                "- Never use `header_injection` when rendering tables.\n"
                "- Do not call `unused_legacy_helper` in this repo.\n"
            ),
        }
        rules = prohibition.extract_added_prohibitions(base_tree, cand_tree)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].targets, ("header_injection",))

        trajs = {
            "base1": "[ACTION] render(mode='header_injection')\n[OBSERVATION] ok\n",
            "inv1": "[ACTION] render(mode='plain')\n[OBSERVATION] ok\n",
            "dead1": "[ACTION] render(mode='header_injection')\n[ERROR] crashed\n",
        }
        rollouts = [
            RunView("base1", "r0", self.root / "b1", ok=True, delivered=True),
            RunView("inv1", "r1", self.root / "i1", ok=True, delivered=True),
            RunView("dead1", "r2", self.root / "d1", ok=False, delivered=False),
        ]
        findings = prohibition.replay_prohibitions(
            "dom1", base_tree, cand_tree, rollouts, lambda rv: trajs[rv.label]
        )
        # Only header_injection matched a delivered rollout (dead1 is excluded).
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].candidate, "dom1")
        self.assertEqual(findings[0].matched_rollouts, ("base1",))
        self.assertEqual(findings[0].status, "unverified_prohibition")

        report = prohibition.format_replay_report(findings)
        self.assertIn("unverified_prohibition", report)
        self.assertIn("`base1`", report)
        self.assertIn("`header_injection`", report)

    def test_blind_compose_withholds_proposer_notes_and_attaches_replay(self):
        adapter = DemoAdapter(self.root)
        # Provide a base tree keyed with POSIX paths (platform-independent test fixture).
        base_tree = {
            "systemprompt.md": "Reconcile the ledger using python scripts/summarize.py.\n",
            "guardrails.md": "",
            "LongTermMEMORY.md": "",
            "tool_descriptions/tool_guidance.md": "",
            "scripts/summarize.py": (adapter.base_harness() / "scripts/summarize.py").read_text(
                encoding="utf-8"
            ),
        }
        base_dir = adapter.execute("ledger-1", None, "base1")
        base_rv = RunView("base1", "base1", base_dir, ok=True, delivered=True)

        seen_decisions: dict[str, list[str]] = {}

        class InspectProposer(DemoProposer):
            def call(self, mission, context_dir, label, **kwargs):
                ctx = Path(context_dir)
                dec_dir = ctx / "decisions"
                seen_decisions[label] = (
                    sorted(p.name for p in dec_dir.iterdir()) if dec_dir.is_dir() else []
                )
                workspace = ctx / "harness"
                script = workspace / "scripts/summarize.py"
                script.write_text(
                    script.read_text(encoding="utf-8").replace(" if amount > 0", ""),
                    encoding="utf-8",
                )
                if label.startswith("dom"):
                    # Prohibit scripts/summarize.py even though base1 ran it and delivered!
                    (workspace / "guardrails.md").write_text(
                        "Never run `scripts/summarize.py` directly.\n", encoding="utf-8"
                    )
                text = f"<notes>Proposer rationale from {label}</notes>"
                reply = Reply(label=label, text=text, ok=True, attempts=1, workspace=workspace)
                self._calls.append(reply)
                return reply

        # On Windows DemoAdapter.validate_tree expects POSIX key 'scripts/summarize.py',
        # so patch read_tree locally to normalize relative paths cleanly cross-platform.
        from unittest.mock import patch

        from harness2.core import tree as HT

        orig_read_tree = HT.read_tree

        def posix_read_tree(surface, d, *, rejected=None):
            stub_set = {rel for rel, _ in surface.stubs}
            local_rejected: list[str] = []
            raw = orig_read_tree(surface, d, rejected=local_rejected)
            if rejected is not None:
                for item in local_rejected:
                    if item.replace("\\", "/") not in stub_set:
                        rejected.append(item)
            return {k.replace("\\", "/"): v for k, v in raw.items()}

        with patch("harness2.core.tree.read_tree", side_effect=posix_read_tree):
            # 1) Default (blind_compose=False, prohibition_replay=False): agg1 sees inv1/dom1 notes.
            seen_decisions.clear()
            prop1 = InspectProposer(adapter.spec.channels["proposer"])
            recursion.run_parallel(
                "ledger-1",
                1,
                [base_rv],
                adapter=adapter,
                prop=prop1,
                work_dir=self.root / "work_default",
                run_id_of=lambda lbl: f"default-{lbl}",
                base_tree=base_tree,
            )
            self.assertEqual(seen_decisions["agg1.a1"], ["dom1.md", "inv1.md"])

            # 2) blind_compose=True, prohibition_replay=True: agg1 sees ONLY prohibition_replay.md!
            seen_decisions.clear()
            prop2 = InspectProposer(adapter.spec.channels["proposer"])
            recursion.run_parallel(
                "ledger-1",
                1,
                [base_rv],
                adapter=adapter,
                prop=prop2,
                work_dir=self.root / "work_blind",
                run_id_of=lambda lbl: f"blind-{lbl}",
                base_tree=base_tree,
                blind_compose=True,
                prohibition_replay=True,
            )
            self.assertEqual(seen_decisions["agg1.a1"], ["prohibition_replay.md"])
            replay_text = (
                self.root / "work_blind/context/agg1/decisions/prohibition_replay.md"
            ).read_text(encoding="utf-8")
            self.assertIn("scripts/summarize.py", replay_text)
            self.assertIn("`base1`", replay_text)


if __name__ == "__main__":
    unittest.main()
