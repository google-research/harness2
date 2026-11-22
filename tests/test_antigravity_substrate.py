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

"""Unit and regression tests for AntigravitySubstrate and the agy_cli proposer channel."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2.core import prompts, tree
from harness2.core.proposer import Proposer, _agy_stdout
from harness2.core.spec import BenchPrompts, BenchSpec, ChannelSpec, resolve_channel_model
from harness2.substrate import ANTIGRAVITY_SURFACE, AntigravitySubstrate, Substrate
from harness2.substrate.antigravity import (
    AGY_RULE_MAX_BYTES,
    final_message,
    render_events,
    stream_usage,
    verify_pinned_policy,
)

SAMPLE_AGY_STREAM = "\n".join(
    [
        json.dumps(
            {
                "event": "init",
                "conversation_id": "conv-1",
                "init": {
                    "model": "gemini-3.8-flash",
                    "cwd": "/ws",
                    "permission_mode": "always-proceed",
                },
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 0,
                    "state": "DONE",
                    "step_type": "user_input",
                },
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 1,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": "read_file",
                    "tool_info": {"name": "read_file", "parameters": {"Path": "/ws/hello.txt"}},
                },
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "read_file",
                    "tool_info": {
                        "name": "read_file",
                        "parameters": {"Path": "/ws/hello.txt"},
                        "output": "MAGIC_VALUE=42",
                    },
                },
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 2,
                    "state": "ACTIVE",
                    "step_type": "agent_response",
                    "text_delta": "Value is ",
                },
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 2,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "text_delta": "42.",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 20,
                        "thinking_tokens": 5,
                        "cache_read_tokens": 500,
                        "total_tokens": 1020,
                    },
                },
            }
        ),
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": "conv-1",
                    "status": "SUCCESS",
                    "response": "Value is 42.",
                    "duration_seconds": 3.5,
                    "num_turns": 1,
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 20,
                        "thinking_tokens": 5,
                        "cache_read_tokens": 500,
                        "total_tokens": 1020,
                    },
                },
            }
        ),
    ]
)


class AntigravitySubstrateTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)

    def _spec(self) -> BenchSpec:
        ch = ChannelSpec("agy_cli", "gemini-3.8-flash", effort="low")
        return BenchSpec(
            name="demo",
            runs_root=self.root / "runs",
            install_mode="agy_root",
            prompts=BenchPrompts(work="verify local code"),
            channels={"proposer": ch, "selector": ch},
        )

    def test_protocol_and_surface_validation(self):
        sub = AntigravitySubstrate()
        self.assertIsInstance(sub, Substrate)
        self.assertEqual(sub.name, "agy")
        self.assertEqual(sub.surface(), ANTIGRAVITY_SURFACE)

        valid_hooks = json.dumps(
            {
                "completion-gate": {
                    "PostToolUse": [
                        {
                            "matcher": "write_file|edit_file",
                            "hooks": [{"type": "command", "command": "python scripts/gate.py PostToolUse"}],
                        }
                    ],
                    "Stop": [{"type": "command", "command": "python scripts/gate.py Stop"}],
                }
            }
        )
        valid_tree = {
            "GEMINI.md": "When checking citations, read .agents/skills/cite_check/SKILL.md.\n",
            ".agents/skills/cite_check/SKILL.md": "---\nname: cite_check\ndescription: check citations\n---\nBody\n",
            ".agents/hooks.json": valid_hooks,
            "scripts/gate.py": "print('{}')\n",
        }
        census = tree.validate(ANTIGRAVITY_SURFACE, valid_tree)
        self.assertEqual(census["skills"], 1)
        self.assertEqual(census["hooks"], 1)
        self.assertEqual(census["scripts"], 1)

        # Codex-style wrapped Stop entry must be rejected on Antigravity's flat Stop schema.
        bad_stop = json.dumps(
            {
                "bad-gate": {
                    "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo ok"}]}]
                }
            }
        )
        with self.assertRaises(ValueError) as ctx:
            tree.validate(ANTIGRAVITY_SURFACE, {"GEMINI.md": "hi\n", ".agents/hooks.json": bad_stop})
        self.assertIn("flat handler list", str(ctx.exception))

        # Unknown event name must be rejected.
        bad_event = json.dumps({"bad": {"OnExit": [{"type": "command", "command": "echo hi"}]}})
        with self.assertRaises(ValueError):
            tree.validate(ANTIGRAVITY_SURFACE, {"GEMINI.md": "hi\n", ".agents/hooks.json": bad_event})

    def test_materialise_and_pinned_policy_integrity(self):
        tree_dir = self.root / "harness"
        tree_dir.mkdir()
        (tree_dir / "GEMINI.md").write_text("# Domain Rules\nVerify every total.\n", encoding="utf-8")
        policy = self.root / "pinned.md"
        policy.write_text("# Core Anti-Hallucination Policy\nNever fabricate citations.\n", encoding="utf-8")

        sub = AntigravitySubstrate(pinned_policy=policy)
        install = sub.materialise(tree_dir, self._spec(), {})
        self.addCleanup(lambda: install.staging_dir.exists() and __import__("shutil").rmtree(install.staging_dir))

        staged_gemini = (install.staging_dir / "GEMINI.md").read_text(encoding="utf-8")
        self.assertIn("Core Anti-Hallucination Policy", staged_gemini)
        self.assertIn("Verify every total.", staged_gemini)
        sha = install.extras["pinned_policy_sha256"]
        self.assertTrue(sha)
        verify_pinned_policy(install.staging_dir, sha)

        # Tampering with the pinned policy block must fail closed.
        (install.staging_dir / "GEMINI.md").write_text(
            staged_gemini.replace("Never fabricate citations.", "Fabricate freely."),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            verify_pinned_policy(install.staging_dir, sha)

        # Oversized pinned + evolved GEMINI.md over 24,000 bytes must fail closed.
        huge = self.root / "huge.md"
        huge.write_text("x" * (AGY_RULE_MAX_BYTES + 100), encoding="utf-8")
        with self.assertRaises(ValueError):
            AntigravitySubstrate(pinned_policy=huge).materialise(tree_dir, self._spec(), {})

    def test_exec_argv_and_stream_rendering(self):
        sub = AntigravitySubstrate(sandbox=True, cli_bin="agy")
        argv = sub.exec_argv(
            "Fix bug", self.root, "gemini-3.8-flash", effort="low", timeout=120
        )
        self.assertIn("-p", argv)
        self.assertIn("stream-json", argv)
        self.assertIn("--sandbox", argv)
        self.assertIn("--print-timeout", argv)
        self.assertIn("120s", argv)

        stream_path = self.root / "stream.jsonl"
        stream_path.write_text(SAMPLE_AGY_STREAM, encoding="utf-8")
        rendered = render_events(stream_path, obs_trunc=4000, max_chars=20000)
        self.assertIn("[ACTION] read_file(", rendered)
        self.assertIn("[OBSERVATION] MAGIC_VALUE=42", rendered)
        self.assertIn("[ASSISTANT] Value is 42.", rendered)
        self.assertIn("total_tokens=1020", rendered)
        self.assertEqual(final_message(stream_path), "Value is 42.")
        usage = stream_usage(stream_path)
        self.assertEqual(usage["status"], "SUCCESS")
        self.assertEqual(usage["total_tokens"], 1020)

    def test_prompts_and_agy_cli_proposer_channel(self):
        bp = BenchPrompts(work="reconcile code and citations")
        p_inv = prompts.propose(bp, "INV", ANTIGRAVITY_SURFACE)
        p_dom = prompts.propose(bp, "DOM", ANTIGRAVITY_SURFACE)
        p_agg = prompts.aggregate(bp, ANTIGRAVITY_SURFACE, k=1)
        p_cmp = prompts.compose_round(bp, ANTIGRAVITY_SURFACE, t=1, k=2)
        p_tts = prompts.propose(
            BenchPrompts(work="reconcile code", prompt_pack="tts"), "INV", ANTIGRAVITY_SURFACE
        )
        self.assertIn("GEMINI.md", p_inv)
        self.assertIn(".agents/hooks.json", p_inv)
        self.assertIn("continuationActive", p_inv)
        self.assertIn("GEMINI.md", p_dom)
        self.assertIn("GEMINI.md", p_agg)
        self.assertIn("GEMINI.md", p_cmp)
        self.assertIn("GEMINI.md", p_tts)

        ch = resolve_channel_model("agy_cli", "gemini-3.8-flash", effort="low")
        self.assertEqual((ch.kind, ch.model), ("agy_cli", "gemini-3.8-flash"))
        with self.assertRaises(ValueError):
            resolve_channel_model("agy_cli", "google-vertex/gemini-3.8-flash")

        text, err = _agy_stdout(SAMPLE_AGY_STREAM)
        self.assertEqual((text, err), ("Value is 42.", ""))

        err_stream = json.dumps(
            {"event": "result", "result": {"status": "ERROR", "error": "model invalid"}}
        )
        text2, err2 = _agy_stdout(err_stream)
        self.assertEqual(text2, "")
        self.assertIn("model invalid", err2)

        prop = Proposer(ch, audit_dir=self.root / "audit")
        with patch("harness2.core.proposer.subprocess.run") as run_mock:
            run_mock.return_value = Mock(stdout=SAMPLE_AGY_STREAM, stderr="", returncode=0)
            reply = prop.call("Check file", None, "inv1")
        self.assertTrue(reply.ok)
        self.assertEqual(reply.text, "Value is 42.")


if __name__ == "__main__":
    unittest.main()
