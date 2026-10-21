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

"""The Claude Code substrate: surface, install layout, flag contract and trajectory grammar."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.core import prompts, tree
from harness2.core.proposer import Proposer
from harness2.core.spec import ChannelSpec, resolve_channel_model
from harness2.substrate.claude_code import (
    CLAUDE_CODE_SURFACE as S,
)
from harness2.substrate.claude_code import (
    ClaudeCodeSubstrate,
    render_events,
    summarize,
)

HOOKS = {
    "hooks": {
        "PostToolUse": [
            {
                "matcher": "Write",
                "hooks": [{"type": "command", "command": "python3 scripts/check.py"}],
            }
        ]
    }
}

TREE = {
    "CLAUDE.md": (
        "Write deliverables to output/.\n- When reconciling ledgers, use the `ledger` skill.\n"
    ),
    ".claude/skills/ledger/SKILL.md": "---\ndescription: reconcile ledgers\n---\nSum both signs.\n",
    ".claude/skills/ledger/scripts/sum.py": "print(1)\n",
    "sub_agents/reader/AGENT.md": "---\nname: wrong\ndescription: reads documents\n---\nRead.\n",
    "scripts/check.py": "print('ok')\n",
    ".claude/hooks.json": json.dumps(HOOKS),
}

STREAM = [
    {"type": "system", "subtype": "init", "model": "claude-x", "skills": ["ledger"], "agents": []},
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "plan the work"},
                {"type": "text", "text": "Reading the ledger."},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
            ]
        },
        "parent_tool_use_id": None,
    },
    {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ledger.csv"}]
        },
    },
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/x"}}
            ]
        },
        "parent_tool_use_id": "t0",
    },
    {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": [{"type": "text", "text": "no such file"}],
                    "is_error": True,
                }
            ]
        },
        "parent_tool_use_id": "t0",
    },
    {
        "type": "system",
        "subtype": "hook_response",
        "hook_name": "PostToolUse:Write",
        "exit_code": 2,
        "stderr": "output/total.txt is empty",
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Done: output/total.txt",
        "num_turns": 3,
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 5},
    },
]


class SpecStub:
    install_mode = "claude_root"


class ClaudeCodeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_surface_validates_full_tree(self):
        census = tree.validate(S, dict(TREE))
        self.assertEqual((census["skills"], census["sub_agents"], census["hooks"]), (1, 1, 1))
        for rel in TREE:
            self.assertTrue(tree.is_allowed(S, rel), rel)
        self.assertFalse(tree.is_allowed(S, ".claude/settings.json"))
        bad = dict(TREE, **{".claude/hooks.json": json.dumps({"hooks": {"BeforeTool": []}})})
        with self.assertRaisesRegex(ValueError, "unknown event"):
            tree.validate(S, bad)
        with self.assertRaisesRegex(ValueError, "empty"):
            tree.validate(S, dict(TREE, **{"CLAUDE.md": ""}))
        prompt_hook = {"hooks": {"Stop": [{"hooks": [{"type": "prompt", "prompt": "Done?"}]}]}}
        tree.validate(S, dict(TREE, **{".claude/hooks.json": json.dumps(prompt_hook)}))
        from harness2.substrate.codex import CODEX_SURFACE

        cx = {"AGENTS.md": "x", ".codex/hooks.json": json.dumps(prompt_hook)}
        with self.assertRaisesRegex(ValueError, 'must be "command"'):
            tree.validate(CODEX_SURFACE, cx)

    def test_materialise_produces_claude_code_layout(self):
        src = tree.write_tree(S, TREE, self.root / "tree")
        inst = ClaudeCodeSubstrate().materialise(src, SpecStub(), {"file:extra/a.txt": "x"})
        self.addCleanup(lambda: __import__("shutil").rmtree(inst.staging_dir, True))
        d = inst.staging_dir
        self.assertTrue((d / "CLAUDE.md").is_file())
        self.assertTrue((d / ".claude/skills/ledger/scripts/sum.py").is_file())
        self.assertTrue((d / "scripts/check.py").is_file())
        self.assertTrue((d / "extra/a.txt").is_file())
        self.assertFalse((d / ".claude/hooks.json").exists())
        self.assertFalse((d / "sub_agents").exists())
        agent = (d / ".claude/agents/reader.md").read_text()
        self.assertTrue(agent.startswith("---\nname: reader\n"), agent)
        self.assertNotIn("name: wrong", agent)
        self.assertIn("description: reads documents", agent)
        self.assertEqual(json.loads((d / ".claude/settings.json").read_text()), HOOKS)
        self.assertEqual(inst.extras["install_mode"], "claude_root")

        class Other:
            install_mode = "codex_root"

        with self.assertRaises(ValueError):
            ClaudeCodeSubstrate().materialise(src, Other(), {})

    def test_exec_argv_isolates_operator_config(self):
        argv = ClaudeCodeSubstrate().exec_argv("go", self.root, "sonnet", effort="high")
        self.assertEqual(argv[1:3], ["-p", "go"])
        for flag in (
            "--dangerously-skip-permissions",
            "--strict-mcp-config",
            "--verbose",
            "--include-hook-events",
        ):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        env = ClaudeCodeSubstrate().exec_env({"CLAUDECODE": "1", "ANTHROPIC_API_KEY": "k"})
        self.assertNotIn("CLAUDECODE", env)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "k")

    def test_trajectory_grammar(self):
        p = self.root / "s.jsonl"
        p.write_text("junk\n" + "\n".join(json.dumps(e) for e in STREAM) + "\n")
        out = render_events(p, obs_trunc=1000, max_chars=10_000)
        for line in (
            "[THINKING] plan the work",
            "[ASSISTANT] Reading the ledger.",
            "[ACTION] Bash(ls)",
            "[OBSERVATION] ledger.csv",
            "[SUBAGENT] [ACTION] Read(/x)",
            "[SUBAGENT] [OBSERVATION ERROR] no such file",
            "[HARNESS] hook PostToolUse:Write exit=2: output/total.txt is empty",
        ):
            self.assertIn(line, out)
        self.assertIn("turns=3", out)
        s = summarize(p)
        self.assertEqual((s["n_events"], s["tool_calls"], s["turns"]), (7, 2, 3))
        self.assertEqual(s["final_text"], "Done: output/total.txt")
        empty = self.root / "e.jsonl"
        empty.write_text("not json\n")
        self.assertEqual(summarize(empty)["n_events"], 0)
        self.assertIn("no parseable", render_events(empty, obs_trunc=10, max_chars=10))

    def test_editor_prompts_name_claude_code_paths(self):
        st = prompts._st(S)
        for text in (st.surface_md, st.tts_surface, st.inv, st.dom):
            self.assertNotIn("AGENTS.md", text)
            self.assertNotIn("codex", text.lower())
        self.assertIn(".claude/hooks.json", st.surface_md)
        self.assertIn("CLAUDE.md", st.tts_surface)

    def test_benchmark_specs(self):
        from harness2.benchmarks.jobbench import CC_BASE_HARNESS, make_spec
        from harness2.benchmarks.lab import lab_spec
        from harness2.cli._common import make_adapter

        self.assertEqual(make_spec("cc").install_mode, "claude_root")
        self.assertTrue(make_spec("cc").allow_subagents)
        self.assertEqual(lab_spec("cc").install_mode, "claude_root")
        self.assertTrue(tree.is_tree(S, CC_BASE_HARNESS))
        with self.assertRaisesRegex(ValueError, "Claude Code"):
            make_adapter("wb", "office", "cc")

    def test_claude_cli_channel_keeps_operator_auth(self):
        spec = ChannelSpec("claude_cli", "sonnet")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k", "CLAUDECODE": "1"}):
            with patch.object(config, "CLAUDE_USE_VERTEX", False):
                env = Proposer(spec).env()
                self.assertEqual(env["ANTHROPIC_API_KEY"], "k")
                self.assertNotIn("CLAUDECODE", env)
                self.assertNotIn("CLAUDE_CODE_USE_VERTEX", env)
            with patch.object(config, "CLAUDE_USE_VERTEX", True):
                env = Proposer(spec).env()
                self.assertNotIn("ANTHROPIC_API_KEY", env)
                self.assertEqual(env["CLAUDE_CODE_USE_VERTEX"], "1")
        with (
            patch.dict(config.PROPOSER_MODEL_BY_CHANNEL, {"claude_cli": "opus"}),
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "claude_cli"),
        ):
            ch = resolve_channel_model(None, None)
            self.assertEqual((ch.kind, ch.model), ("claude_cli", "opus"))
        with self.assertRaises(ValueError):
            resolve_channel_model("claude_cli", "anthropic/opus")

    def test_claude_cli_session_excludes_edited_harness(self):
        class Done:
            returncode = 0
            stdout = json.dumps({"result": "<notes>ok</notes>", "subtype": "success"})
            stderr = ""

        with patch("harness2.core.proposer.subprocess.run", return_value=Done()) as run:
            text, _, err = Proposer(ChannelSpec("claude_cli", "sonnet"))._claude_cli(
                self.root, "mission", "", 60
            )
        argv = run.call_args.args[0]
        self.assertEqual((text, err), ("<notes>ok</notes>", ""))
        self.assertIn("--disable-slash-commands", argv)
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertIn("**/context/**", settings["claudeMdExcludes"])
        with (
            patch.dict(os.environ, {"CLAUDECODE": "1"}),
            patch.object(config, "CLAUDE_USE_VERTEX", True),
        ):
            self.assertNotIn("CLAUDECODE", Proposer(ChannelSpec("claude_cli", "sonnet")).env())

    def test_lab_cc_uses_claude_effort(self):
        from harness2.benchmarks.lab import LabAdapter

        with patch.object(config, "CLAUDE_EFFORT", "medium"):
            self.assertEqual(LabAdapter(substrate="cc").solver_effort, "medium")
            self.assertEqual(LabAdapter(substrate="cc", solver_effort="low").solver_effort, "low")
            self.assertEqual(LabAdapter(substrate="oc").solver_effort, "high")

    def test_validate_models_for_claude_code_cell(self):
        with (
            patch.multiple(
                config,
                SOLVER_MODEL_CC="sonnet",
                CLAUDE_IMPROVER_MODEL="opus",
                DEFAULT_PROPOSER_CHANNEL="claude_cli",
                SOLVER_MODEL_OC="",
                CLAUDE_USE_VERTEX=False,
            ),
            patch.dict(os.environ, {"HARNESS2_CLAUDE_MODEL": "sonnet"}),
        ):
            config.validate_models(substrate="cc")
            self.assertFalse(config.uses_opencode("cc"))
            self.assertTrue(config.uses_opencode("oc"))
            with patch.object(config, "SOLVER_MODEL_CC", "anthropic/sonnet"):
                with self.assertRaises(config.ConfigError):
                    config.validate_models(substrate="cc")


if __name__ == "__main__":
    unittest.main()
