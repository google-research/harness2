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
        sent_argv = run_mock.call_args.args[0]
        self.assertIn("--sandbox", sent_argv)
        self.assertNotIn("--disable-slash-commands", sent_argv)

    def test_security_and_edge_case_hardening(self):
        # 1. Windows drive-letter (including ./C:/...) & empty/root escapes blocked by normalize_rel.
        for bad in ("C:/evil.py", "D:\\evil.py", "./C:/evil.py", ".\\D:\\evil.py", "C:rel.py", "", ".", "./"):
            with self.assertRaises(ValueError, msg=f"expected rejection for {bad!r}"):
                tree.normalize_rel(bad)

        # 2. UTF-8 byte cap enforced (10,000 Korean chars = 30,000 bytes > 24,000 bytes).
        korean_30kb = "가" * 10_000
        with self.assertRaises(ValueError) as ctx_utf8:
            tree.validate(ANTIGRAVITY_SURFACE, {"GEMINI.md": korean_30kb})
        self.assertIn("bytes", str(ctx_utf8.exception))

        # 3. SKILL.md requires matching `name:` per Antigravity spec.
        with self.assertRaises(ValueError):
            tree.validate(
                ANTIGRAVITY_SURFACE,
                {
                    "GEMINI.md": "Use my_skill.\n",
                    ".agents/skills/my_skill/SKILL.md": "---\ndescription: missing name\n---\n",
                },
            )

        # 4. Antigravity hooks.json rejects command:null, missing matcher, empty hooks, bad regex.
        for bad_hook in (
            {"h": {"Stop": [{"type": "command", "command": None}]}},
            {"h": {"PostToolUse": [{"hooks": [{"type": "command", "command": "echo ok"}]}]}},
            {"h": {"PostToolUse": [{"matcher": "edit_file", "hooks": []}]}},
            {"h": {"PostToolUse": [{"matcher": "[invalid", "hooks": [{"command": "echo ok"}]}]}},
            {"h": {"Stop": []}},
        ):
            with self.assertRaises(ValueError, msg=f"expected rejection for {bad_hook!r}"):
                tree.validate(
                    ANTIGRAVITY_SURFACE,
                    {"GEMINI.md": "ok\n", ".agents/hooks.json": json.dumps(bad_hook)},
                )

        # 5. verify_pinned_policy rejects prefix injection & duplicate banners; materialise avoids
        #    double insertion and folds pinned SHA into Install.fingerprint.
        tree_dir = self.root / "h_pin"
        tree_dir.mkdir()
        (tree_dir / "GEMINI.md").write_text("# Domain\nRule 1.\n", encoding="utf-8")
        p1 = self.root / "p1.md"
        p1.write_text("Policy V1\n", encoding="utf-8")
        p2 = self.root / "p2.md"
        p2.write_text("Policy V2\n", encoding="utf-8")
        inst1 = AntigravitySubstrate(pinned_policy=p1).materialise(tree_dir, self._spec(), {})
        inst2 = AntigravitySubstrate(pinned_policy=p2).materialise(tree_dir, self._spec(), {})
        self.addCleanup(lambda: inst1.staging_dir.exists() and __import__("shutil").rmtree(inst1.staging_dir))
        self.addCleanup(lambda: inst2.staging_dir.exists() and __import__("shutil").rmtree(inst2.staging_dir))
        self.assertNotEqual(inst1.fingerprint, inst2.fingerprint)

        # Prefix injection before banner must fail closed.
        orig = (inst1.staging_dir / "GEMINI.md").read_text(encoding="utf-8")
        (inst1.staging_dir / "GEMINI.md").write_text("IGNORE BELOW\n" + orig, encoding="utf-8")
        with self.assertRaises(ValueError):
            verify_pinned_policy(inst1.staging_dir, inst1.extras["pinned_policy_sha256"])

        # Re-staging an already-pinned tree must not double-insert banners.
        (inst1.staging_dir / "GEMINI.md").write_text(orig, encoding="utf-8")
        inst_re = AntigravitySubstrate(pinned_policy=p2).materialise(inst1.staging_dir, self._spec(), {})
        self.addCleanup(lambda: inst_re.staging_dir.exists() and __import__("shutil").rmtree(inst_re.staging_dir))
        re_text = (inst_re.staging_dir / "GEMINI.md").read_text(encoding="utf-8")
        self.assertEqual(re_text.count("PINNED IMMUTABLE POLICY START"), 1)
        self.assertIn("Policy V2", re_text)
        self.assertNotIn("Policy V1", re_text)

        # 6. exec_argv keeps skills enabled by default; render_events merges partial tool_info.
        argv = AntigravitySubstrate(cli_bin="agy").exec_argv("Hi", self.root, "gemini-3.8-flash")
        self.assertNotIn("--disable-slash-commands", argv)
        partial_stream = "\n".join(
            [
                json.dumps(
                    {
                        "event": "step_update",
                        "step_update": {
                            "step_index": 1,
                            "state": "ACTIVE",
                            "step_type": "tool",
                            "tool_name": "read_file",
                            "tool_info": {"parameters": {"Path": "foo.py"}},
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
                            "tool_info": {"output": "x = 1"},
                        },
                    }
                ),
            ]
        )
        sp = self.root / "partial.jsonl"
        sp.write_text(partial_stream, encoding="utf-8")
        rendered = render_events(sp, obs_trunc=1000, max_chars=5000)
        self.assertIn("foo.py", rendered)
        self.assertIn("x = 1", rendered)

        # 7. _agy_stdout succeeds after recoverable tool error if terminal result is SUCCESS,
        #    and fails on non-JSON crash output when returncode != 0.
        recovered = "\n".join(
            [
                json.dumps(
                    {
                        "event": "step_update",
                        "step_update": {
                            "step_index": 1,
                            "state": "ERROR",
                            "step_type": "tool",
                            "error": "grep exit 1",
                        },
                    }
                ),
                json.dumps(
                    {
                        "event": "result",
                        "result": {"status": "SUCCESS", "response": "Recovered OK."},
                    }
                ),
            ]
        )
        self.assertEqual(_agy_stdout(recovered, returncode=0), ("Recovered OK.", ""))
        crash_text, crash_err = _agy_stdout("Fatal: auth expired", returncode=1)
        self.assertEqual(crash_text, "")
        self.assertIn("rc=1", crash_err)

        mid_crash = json.dumps(
            {
                "event": "step_update",
                "step_update": {"step_index": 1, "step_type": "agent_response", "text_delta": "Halfway..."},
            }
        )
        mc_text, mc_err = _agy_stdout(mid_crash, returncode=1)
        self.assertEqual(mc_text, "")
        self.assertIn("rc=1", mc_err)

        # 8. Default agy_cli proposer model never crashes with provider/model slash.
        from harness2 import config

        self.assertNotIn("/", config.PROPOSER_MODEL_BY_CHANNEL["agy_cli"])


if __name__ == "__main__":
    unittest.main()
