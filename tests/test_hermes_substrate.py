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

"""Unit and workflow tests for HermesSubstrate and HERMES_SURFACE (#117)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2.core import prompts, schedule, tree
from harness2.core.spec import BenchPrompts, SubstrateLike
from harness2.demo import DemoAdapter, DemoProposer
from harness2.substrate import HERMES_SURFACE, HermesSubstrate, Substrate
from harness2.substrate.hermes import render_events, stage_workspace


class HermesSubstrateTests(unittest.TestCase):
    def test_protocol_and_surface_validation(self):
        sub = HermesSubstrate()
        self.assertIsInstance(sub, Substrate)
        self.assertIsInstance(sub, SubstrateLike)
        self.assertEqual(sub.name, "hm")
        self.assertEqual(sub.surface(), HERMES_SURFACE)

        candidate = {
            "SOUL.md": "Follow instructions carefully.",
            "skills/ledger-audit/SKILL.md": (
                "---\nname: ledger-audit\ndescription: Reconcile signed ledger rows.\n---\n"
                "Run `python3 skills/ledger-audit/scripts/check.py`.\n"
            ),
            "skills/ledger-audit/scripts/check.py": "print('ok')\n",
            "scripts/summarize.py": "print('Total: 0.00')\n",
            "sub_agents/reviewer/AGENT.md": "---\ndescription: Check anomalies.\n---\nReview.\n",
        }
        repaired = tree.autotrigger(HERMES_SURFACE, candidate)
        self.assertEqual(repaired, ["ledger-audit"])
        self.assertIn("load the `ledger-audit` skill", candidate["SOUL.md"])
        census = tree.validate(HERMES_SURFACE, candidate)
        self.assertEqual(census["skills"], 1)
        self.assertEqual(census["scripts"], 1)
        self.assertEqual(census["sub_agents"], 1)

        with self.assertRaisesRegex(ValueError, "SOUL.md is empty"):
            tree.validate(HERMES_SURFACE, {"SOUL.md": "   "})

    def test_prompts_support_hermes_surface_across_packs(self):
        for pack in ("adapt", "tts"):
            bp = BenchPrompts(work="reconcile ledger entries", prompt_pack=pack)
            for dim in ("INV", "DOM"):
                p = prompts.propose(bp, dim, HERMES_SURFACE, k=2, index=1)
                self.assertIn("SOUL.md", p)
            self.assertIn("SOUL.md", prompts.aggregate(bp, HERMES_SURFACE, k=2, index=1))
            self.assertIn("SOUL.md", prompts.compose_round(bp, HERMES_SURFACE, t=1, k=2))

    def test_materialise_stages_isolated_hermes_home_and_workspace_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "tree"
            tree.write_tree(
                HERMES_SURFACE,
                {
                    "SOUL.md": "Use ledger-audit when needed.\n",
                    "skills/ledger-audit/SKILL.md": (
                        "---\nname: ledger-audit\ndescription: Audit rows.\n---\nBody\n"
                    ),
                    "scripts/summarize.py": "print(42)\n",
                },
                src,
                stubs=False,
            )
            sub = HermesSubstrate()
            with self.assertRaisesRegex(ValueError, "hermes_home"):
                sub.materialise(src, SimpleNamespace(install_mode="codex_root"), {})

            inst = sub.materialise(
                src,
                SimpleNamespace(install_mode="hermes_home"),
                {"file:config/extra.txt": "extra", "tag": "unit"},
            )
            try:
                hhome = Path(inst.extras["hermes_home"])
                self.assertTrue((hhome / "SOUL.md").is_file())
                self.assertTrue((hhome / "skills/ledger-audit/SKILL.md").is_file())
                self.assertFalse((hhome / "scripts").exists())

                ws = root / "ws"
                stage_workspace(inst, ws)
                self.assertTrue((ws / "scripts/summarize.py").is_file())
                self.assertEqual((ws / "config/extra.txt").read_text(encoding="utf-8"), "extra")

                env = sub.chat_env(
                    {"ANTHROPIC_API_KEY": "secret", "OPENAI_API_KEY": "sk-keep", "PATH": "/bin"},
                    hermes_home=hhome,
                    model="openai/gpt-5",
                )
                self.assertEqual(env["HERMES_HOME"], str(hhome))
                self.assertNotIn("ANTHROPIC_API_KEY", env)
                self.assertEqual(env["OPENAI_API_KEY"], "sk-keep")
            finally:
                shutil.rmtree(inst.staging_dir, ignore_errors=True)

    def test_render_events_coalesces_text_deltas_and_handles_replay_gap_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            stream = Path(tmp) / "hermes.jsonl"
            events = [
                {"type": "system", "subtype": "init", "model": "gemini", "session_id": "s1"},
                {"type": "text", "text": "جاري فحص "},
                {"type": "text", "text": "دفتر الأستاذ."},
                {
                    "type": "tool_use",
                    "name": "terminal",
                    "tool_call_id": "call_1",
                    "input": {"command": "python3 scripts/summarize.py ledger.csv"},
                },
                {
                    "type": "tool_result",
                    "name": "terminal",
                    "tool_call_id": "call_1",
                    "output": "Total: 85.15\n",
                    "duration_ms": 18,
                    "is_error": False,
                },
                {
                    "type": "tool_use",
                    "name": "read_file",
                    "tool_call_id": "call_2",
                    "input": {"path": "missing.csv"},
                },
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "tool_call_id": "call_2",
                    "output": "FileNotFoundError: missing.csv",
                    "duration_ms": 3,
                    "is_error": True,
                },
                {"type": "text", "text": "الإجمالي الصحيح هو "},
                {"type": "text", "text": "85.15."},
                {
                    "type": "result",
                    "session_id": "s1",
                    "exit_code": 0,
                    "text": "الإجمالي الصحيح هو 85.15.",
                    "tokens": {"input": 100, "output": 25, "total": 125},
                },
            ]
            stream.write_text(
                "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events) + "\n",
                encoding="utf-8",
            )
            rendered = render_events(stream, obs_trunc=500, max_chars=5000)
            self.assertEqual(rendered.count("[ASSISTANT]"), 2)
            self.assertIn("[ASSISTANT] جاري فحص دفتر الأستاذ.", rendered)
            self.assertIn(
                '[ACTION] terminal({"command": "python3 scripts/summarize.py ledger.csv"})',
                rendered,
            )
            self.assertIn("[OBSERVATION] Total: 85.15", rendered)
            self.assertIn("[OBSERVATION ERROR] FileNotFoundError: missing.csv", rendered)
            self.assertIn("[ASSISTANT] الإجمالي الصحيح هو 85.15.", rendered)

            gap_stream = Path(tmp) / "gap.jsonl"
            gap_events = [
                {"type": "system", "subtype": "init", "model": "m", "session_id": "s2"},
                {
                    "type": "result",
                    "session_id": "s2",
                    "exit_code": 1,
                    "text": "Authoritative post-gap reply.",
                    "error": "upstream timeout",
                    "replay_gap": True,
                },
            ]
            gap_stream.write_text(
                "\n".join(json.dumps(ev) for ev in gap_events) + "\n", encoding="utf-8"
            )
            gap_rendered = render_events(gap_stream, obs_trunc=500, max_chars=5000)
            self.assertIn("[ASSISTANT] Authoritative post-gap reply.", gap_rendered)
            self.assertIn("[ERROR] upstream timeout", gap_rendered)

    def test_preflight_checks_hermes_binary_without_starting_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "fake-hermes"
            fake_bin.write_text("#!/bin/sh\necho 'Hermes Agent v0.23.3'\n", encoding="utf-8")
            fake_bin.chmod(0o755)
            with patch.dict(os.environ, {"HARNESS2_HERMES_BIN": str(fake_bin)}):
                HermesSubstrate().preflight()

            bad_bin = Path(tmp) / "bad-hermes"
            bad_bin.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
            bad_bin.chmod(0o755)
            with patch.dict(os.environ, {"HARNESS2_HERMES_BIN": str(bad_bin)}):
                with self.assertRaisesRegex(RuntimeError, "exited 2"):
                    HermesSubstrate().preflight()

    def test_end_to_end_recursive_workflow_over_hermes_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = DemoAdapter(root, substrate=HermesSubstrate())
            proposer = DemoProposer(adapter.spec.channels["proposer"])
            summary = schedule.run_domain(
                "accounting",
                adapter.tasks(),
                k=2,
                adapter=adapter,
                prop=proposer,
                runs_root=adapter.spec.runs_root / "adapt",
                library_root=adapter.spec.runs_root / "library" / "demo" / "k2-hm",
                modes=("agg", "seq"),
            )
            self.assertEqual(summary["done"], 2)
            self.assertEqual(summary["failed"], 0)
            lib_soul = (
                adapter.spec.runs_root
                / "library/demo/k2-hm/agg/accounting/harnesses/ledger-1/harness/SOUL.md"
            )
            self.assertTrue(lib_soul.is_file())


if __name__ == "__main__":
    unittest.main()
