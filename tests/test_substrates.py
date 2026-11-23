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

"""The Claude Code and Pi substrates: surface, install layout, env isolation, renderers."""

import itertools
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks import jb_cli_runner, jb_opencode_runner
from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.benchmarks.lab import LabAdapter
from harness2.core import prompts, tree
from harness2.core.proposer import Proposer
from harness2.core.spec import ChannelSpec
from harness2.substrate import (
    CLAUDE_SURFACE,
    PI_SURFACE,
    ClaudeSubstrate,
    CodexSubstrate,
    OpencodeSubstrate,
    PiSubstrate,
)

SKILL = "---\nname: pdf\ndescription: read a PDF\n---\nUse pdftotext.\n"
# The body's own `name:` and `---` lines must not be mistaken for frontmatter.
SUBAGENT = "---\ndescription: summarise a long file\n---\nname: not this\n---\nFive bullets.\n"
HOOKS = {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}]}
# What Claude Code returns as the result text when a subscription's usage limit is reached.
LIMIT_REPLY = "You've hit your session limit · resets 1:20pm"


class SubstrateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def materialise(self, substrate, files: dict, mode: str) -> Path:
        src = self.root / "tree"
        tree.validate(substrate.surface(), files)
        tree.write_tree(substrate.surface(), files, src)
        staged = substrate.materialise(src, SimpleNamespace(install_mode=mode), {}).staging_dir
        self.addCleanup(shutil.rmtree, staged, True)
        return staged

    def test_claude_install_maps_subagents_and_keeps_only_hooks(self):
        settings = {"hooks": HOOKS, "permissions": {"allow": ["Bash(*)"]}, "env": {"X": "1"}}
        staged = self.materialise(
            ClaudeSubstrate(),
            {
                "CLAUDE.md": "Work carefully.\n- When reading a PDF, use the `pdf` skill.\n",
                ".claude/skills/pdf/SKILL.md": SKILL,
                "sub_agents/summary/AGENT.md": SUBAGENT,
                ".claude/settings.json": json.dumps(settings),
            },
            "claude_root",
        )
        self.assertEqual(
            json.loads((staged / ".claude/settings.json").read_text()), {"hooks": HOOKS}
        )
        agent = (staged / ".claude/agents/summary.md").read_text()
        self.assertEqual(agent, "---\nname: summary\n" + SUBAGENT[4:])
        self.assertTrue((staged / ".claude/skills/pdf/SKILL.md").is_file())
        self.assertFalse((staged / "sub_agents").exists())

    def test_pi_install_keeps_stub_out_of_extensions(self):
        staged = self.materialise(
            PiSubstrate(),
            {
                "AGENTS.md": "Work carefully.\n",
                ".pi/extensions/guard.ts": "export default () => {}",
            },
            "pi_root",
        )
        self.assertEqual(
            sorted(p.name for p in (staged / ".pi/extensions").iterdir()), ["guard.ts"]
        )
        self.assertTrue((staged / "AGENTS.md").is_file())

    def test_wrong_install_mode_is_refused(self):
        with self.assertRaises(ValueError):
            ClaudeSubstrate().materialise(self.root, SimpleNamespace(install_mode="pi_root"), {})
        with self.assertRaises(ValueError):
            PiSubstrate().materialise(self.root, SimpleNamespace(install_mode="codex_root"), {})

    @patch.object(config, "VERTEX_PROJECT", "p")
    @patch.object(config, "CLAUDE_AUTH", "vertex")
    def test_host_credentials_never_reach_a_run(self):
        host = {"PATH": "/bin", "ANTHROPIC_API_KEY": "sk-host", "CLAUDE_CODE_USE_BEDROCK": "1"}
        env = ClaudeSubstrate().exec_env(host, config_dir=self.root)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.root))
        self.assertEqual(env["CLAUDE_CODE_USE_VERTEX"], "1")
        argv = PiSubstrate().exec_argv("task", self.root, "google-vertex/m")
        self.assertIn("--no-skills", argv)  # pi would also load $HOME/.agents/skills
        self.assertEqual(argv[argv.index("--skill") + 1], str(self.root / ".agents/skills"))
        self.assertEqual(
            PiSubstrate().exec_env(host, config_dir=self.root)["PI_CODING_AGENT_DIR"],
            str(self.root),
        )

    def test_claude_auth_passes_only_the_named_credential(self):
        host = {
            "PATH": "/bin",
            "ANTHROPIC_API_KEY": "sk-host",
            "CLAUDE_CODE_OAUTH_TOKEN": "oat-host",
            "CLAUDE_CODE_USE_VERTEX": "1",
        }
        cases = {
            "api_key": ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"),
            "oauth": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
        }
        for mode, (kept, dropped) in cases.items():
            with (
                self.subTest(mode=mode),
                patch.object(config, "CLAUDE_AUTH", mode),
                patch.dict(config._CLAUDE_SECRETS, {}, clear=True),
                patch.dict("os.environ", {kept: "secret"}),
            ):
                env = ClaudeSubstrate().exec_env(host, config_dir=self.root)
                self.assertEqual(env[kept], "secret")  # from the harness env, not the host map
                self.assertNotIn(dropped, env)
                self.assertNotIn("CLAUDE_CODE_USE_VERTEX", env)
                prop = Proposer(ChannelSpec("claude_cli", "claude-x")).env()
                self.assertEqual(prop[kept], "secret")
                self.assertNotIn("CLAUDE_CODE_USE_VERTEX", prop)
                self.assertEqual(prop["ANTHROPIC_MODEL"], "claude-x")

    def test_claude_auth_refuses_a_missing_credential_or_unknown_mode(self):
        for mode in ("oauth", "api_key", "subscription"):
            with (
                self.subTest(mode=mode),
                patch.object(config, "CLAUDE_AUTH", mode),
                patch.dict(config._CLAUDE_SECRETS, {}, clear=True),
                patch.dict("os.environ", {}, clear=True),
                self.assertRaises(config.ConfigError),
            ):
                config.claude_auth_env()

    def test_claude_only_needs_no_opencode_model(self):
        token = {"HARNESS2_CLAUDE_MODEL": "claude-x", "CLAUDE_CODE_OAUTH_TOKEN": "secret"}
        with (
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "claude_cli"),
            patch.object(config, "CLAUDE_AUTH", "oauth"),
            patch.dict(config._CLAUDE_SECRETS, {}, clear=True),
            patch.dict(config.PROPOSER_MODEL_BY_CHANNEL, {"claude_cli": "claude-x"}),
            patch.dict("os.environ", token, clear=True),
        ):
            config.validate_models(substrate="cc")  # no HARNESS2_SOLVER_MODEL
            with self.assertRaises(config.ConfigError):
                config.validate_models(substrate="oc")
            with (
                patch.dict(config.PROPOSER_MODEL_BY_CHANNEL, {"claude_cli": "anthropic/claude-x"}),
                self.assertRaises(config.ConfigError),
            ):
                config.validate_models(substrate="cc")

    def test_subscription_token_is_not_left_in_the_process_env(self):
        with (
            patch.object(config, "CLAUDE_AUTH", "oauth"),
            patch.dict(config._CLAUDE_SECRETS, {"CLAUDE_CODE_OAUTH_TOKEN": "oat-from-dotenv"}),
            patch.dict("os.environ", {}, clear=True),
        ):
            self.assertEqual(
                config.claude_auth_env(), {"CLAUDE_CODE_OAUTH_TOKEN": "oat-from-dotenv"}
            )
            self.assertNotIn(
                "CLAUDE_CODE_OAUTH_TOKEN", PiSubstrate().exec_env({}, config_dir=self.root)
            )
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", os.environ)

    def token_pool(self, *tokens):
        """oauth mode with HARNESS2_CLAUDE_OAUTH_TOKENS set to `tokens`, none limited yet."""
        for p in (
            patch.object(config, "CLAUDE_AUTH", "oauth"),
            patch.object(config, "_CLAUDE_OAUTH_POOL", list(tokens)),
            patch.object(config, "_CLAUDE_LIMITED", set()),
            patch.object(config, "_CLAUDE_TURN", itertools.count()),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_oauth_token_pool_takes_turns_and_skips_a_limited_token(self):
        self.token_pool("oat-a", "oat-b")
        used = [config.claude_auth_env()["CLAUDE_CODE_OAUTH_TOKEN"] for _ in range(3)]
        self.assertEqual(used, ["oat-a", "oat-b", "oat-a"])
        self.assertFalse(config.claude_usage_limited("Done.", {"CLAUDE_CODE_OAUTH_TOKEN": "oat-a"}))
        self.assertTrue(
            config.claude_usage_limited(LIMIT_REPLY, {"CLAUDE_CODE_OAUTH_TOKEN": "oat-a"})
        )
        used = [config.claude_auth_env()["CLAUDE_CODE_OAUTH_TOKEN"] for _ in range(2)]
        self.assertEqual(used, ["oat-b", "oat-b"])
        config.claude_usage_limited(LIMIT_REPLY, {"CLAUDE_CODE_OAUTH_TOKEN": "oat-b"})
        with self.assertRaises(config.ConfigError):
            config.claude_auth_env()

    def test_without_a_token_pool_a_limit_reply_changes_nothing(self):
        with (
            patch.object(config, "CLAUDE_AUTH", "oauth"),
            patch.object(config, "_CLAUDE_OAUTH_POOL", []),
            patch.dict(config._CLAUDE_SECRETS, {"CLAUDE_CODE_OAUTH_TOKEN": "oat-one"}),
        ):
            env = config.claude_auth_env()
            self.assertFalse(config.claude_usage_limited(LIMIT_REPLY, env))
            self.assertEqual(config.claude_auth_env(), {"CLAUDE_CODE_OAUTH_TOKEN": "oat-one"})

    def test_proposer_retries_a_limited_reply_on_the_next_token(self):
        self.token_pool("oat-a", "oat-b")
        replies = iter(
            [
                {"type": "result", "subtype": "success", "is_error": True, "result": LIMIT_REPLY},
                {"type": "result", "subtype": "success", "result": "edited"},
            ]
        )
        seen = []

        def fake_run(cmd, **kw):
            seen.append(kw["env"]["CLAUDE_CODE_OAUTH_TOKEN"])
            return SimpleNamespace(returncode=0, stdout=json.dumps(next(replies)), stderr="")

        with (
            patch("harness2.core.proposer.subprocess.run", fake_run),
            patch("harness2.core.proposer.time.sleep"),
        ):
            reply = Proposer(ChannelSpec("claude_cli", "claude-x")).call("m", None, "l")
        self.assertEqual(reply.text, "edited")
        self.assertEqual(seen, ["oat-a", "oat-b"])
        self.assertEqual(config._CLAUDE_LIMITED, {"oat-a"})

    def test_cli_runner_marks_a_limited_token(self):
        self.token_pool("oat-a", "oat-b")
        cfg = SimpleNamespace(substrate=ClaudeSubstrate(), model="m", timeout_per_task="60")
        (self.root / "ws").mkdir()

        def fake_run(argv, **kw):
            kw["stdout"].write(json.dumps({"type": "result", "result": LIMIT_REPLY}) + "\n")
            return SimpleNamespace(returncode=1)

        with patch.object(jb_cli_runner.subprocess, "run", fake_run):
            jb_cli_runner._attempt(
                cfg, self.root / "ws", self.root / "t.jsonl", self.root / "log", "t1", 1
            )
        self.assertEqual(config._CLAUDE_LIMITED, {"oat-a"})

    def render(self, substrate, events: list[dict]) -> str:
        stream = self.root / "stream.jsonl"
        lines = (json.dumps(e, ensure_ascii=False) for e in events)
        stream.write_text("noise\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return substrate.read_trajectory(stream, obs_trunc=10_000, max_chars=100_000)

    def test_claude_renderer(self):
        out = self.render(
            ClaudeSubstrate(),
            [
                {"type": "system", "subtype": "init"},
                {"type": "system", "subtype": "api_retry", "attempt": 1, "error": "auth"},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "plan"},
                            {"type": "text", "text": "Reading\u2028on."},
                            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {"type": "tool_result", "content": "no such file", "is_error": True},
                        ]
                    },
                },
                {
                    "duration_api_ms": 0,  # real streams do not lead with "type"
                    "type": "result",
                    "subtype": "error_max_turns",
                    "is_error": True,
                    "errors": ["max turns"],
                    "num_turns": 3,
                    "usage": {},
                },
            ],
        )
        self.assertEqual(
            out.split("\n"),
            [
                "[HARNESS] api_retry attempt=1: auth",
                "[REASONING] plan",
                "[ASSISTANT] Reading\u2028on.",
                '[ACTION] Bash({"command": "ls"})',
                "[OBSERVATION ERROR] no such file",
                "[HARNESS] result error_max_turns | turns=3 tokens in=None out=None cost_usd=None",
                "[ERROR] max turns",
            ],
        )

    def test_pi_renderer_surfaces_failure_despite_exit_zero(self):
        out = self.render(
            PiSubstrate(),
            [
                {"type": "session", "version": 3},
                {"type": "message_end", "message": {"role": "user", "content": "Do the task"}},
                {"type": "tool_execution_start", "toolName": "bash", "args": {"command": "ls"}},
                {
                    "type": "tool_execution_end",
                    "toolName": "bash",
                    "isError": False,
                    "result": {"content": [{"type": "text", "text": "a.txt"}]},
                },
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "stopReason": "error",
                        "errorMessage": "quota",
                        "content": [
                            {"type": "text", "text": "Done?"},
                            {"type": "toolCall", "name": "bash", "arguments": {}},
                        ],
                    },
                },
            ],
        )
        self.assertEqual(
            out.splitlines(),
            [
                "[USER] Do the task",
                '[ACTION] bash({"command": "ls"})',
                "[OBSERVATION] a.txt",
                "[ASSISTANT] Done?",
                "[ERROR] error: quota",
            ],
        )

    def test_prompt_surfaces_name_their_own_paths(self):
        claude = prompts._st(CLAUDE_SURFACE)
        pi = prompts._st(PI_SURFACE)
        for text in (claude.surface_md, claude.tts_surface, claude.inv, claude.dom):
            for stale in ("AGENTS.md", ".agents/", ".codex"):
                self.assertNotIn(stale, text)
        for text in (pi.surface_md, pi.tts_surface, pi.inv):
            self.assertNotIn(".codex", text)
            self.assertIn(".pi/extensions/", text)

    def test_jobbench_dispatch_never_falls_through_to_codex(self):
        expected = {
            "oc": OpencodeSubstrate,
            "cx": CodexSubstrate,
            "cc": ClaudeSubstrate,
            "pi": PiSubstrate,
        }
        for name, cls in expected.items():
            adapter = JobBenchAdapter(substrate=name, store_root=self.root)
            self.assertIs(type(adapter.substrate), cls)
            self.assertEqual(adapter.substrate.name, name)
        self.assertTrue(JobBenchAdapter(substrate="cc", store_root=self.root).spec.allow_subagents)
        self.assertFalse(JobBenchAdapter(substrate="pi", store_root=self.root).spec.allow_subagents)
        with self.assertRaises(ValueError):
            JobBenchAdapter(substrate="xx", store_root=self.root)
        with self.assertRaises(ValueError):
            LabAdapter(substrate="cc")

    def test_cli_runner_stages_harness_root_and_keeps_deliverables(self):
        task = self.root / "store/acct/t1/task_folder"
        task.mkdir(parents=True)
        (task / "TASK_INSTRUCTIONS.txt").write_text("Do it.")
        render = self.root / "render"
        render.mkdir()
        (render / "CLAUDE.md").write_text("Work.")
        cfg = jb_cli_runner.Settings(
            task_ids=("acct/t1",),
            label="l1",
            render_dir=render,
            tasks_base=self.root / "store",
            substrate=ClaudeSubstrate(),
            model="m",
            timeout_per_task="60",
            max_retries=1,
            log_dir=self.root / "logs",
        )
        cfg.log_dir.mkdir()
        seen = []

        def fake_cli(cfg, workspace, traj_file, log_file, task_name, attempt):
            seen.extend(sorted(p.name for p in workspace.iterdir()))
            (workspace / "output/report.txt").write_text("done")
            return 0

        with patch.object(jb_opencode_runner, "TEMP_BASE", self.root / "ws"):
            delivered = jb_opencode_runner.run_one(
                cfg, "acct/t1", stage=jb_cli_runner._stage, run=fake_cli
            )
        self.assertTrue(delivered)
        self.assertEqual(seen, ["CLAUDE.md", "output", "task_folder"])
        self.assertTrue((self.root / "store/acct/t1/model_output/l1/report.txt").is_file())


if __name__ == "__main__":
    unittest.main()
