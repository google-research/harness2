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

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2.benchmarks import jb_cli_runner, jb_opencode_runner
from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.benchmarks.lab import LabAdapter
from harness2.core import prompts, tree
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

    def test_host_credentials_never_reach_a_run(self):
        host = {
            "PATH": "/bin",
            "ANTHROPIC_API_KEY": "sk-host",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "HARNESS2_JUDGE_API_KEY": "judge-secret",
        }
        env = ClaudeSubstrate().exec_env(host, config_dir=self.root)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", env)
        self.assertNotIn("HARNESS2_JUDGE_API_KEY", env)
        self.assertEqual(env["PATH"], "/bin")
        self.assertNotIn(
            "HARNESS2_JUDGE_API_KEY", PiSubstrate().exec_env(host, config_dir=self.root)
        )
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.root))
        self.assertEqual(env["CLAUDE_CODE_USE_VERTEX"], "1")
        argv = PiSubstrate().exec_argv("task", self.root, "google-vertex/m")
        self.assertIn("--no-skills", argv)  # pi would also load $HOME/.agents/skills
        self.assertEqual(argv[argv.index("--skill") + 1], str(self.root / ".agents/skills"))
        self.assertEqual(
            PiSubstrate().exec_env(host, config_dir=self.root)["PI_CODING_AGENT_DIR"],
            str(self.root),
        )

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

    def runner_cfg(self, label="l1", max_retries=1):
        task = self.root / "store/acct/t1/task_folder"
        task.mkdir(parents=True, exist_ok=True)
        (task / "TASK_INSTRUCTIONS.txt").write_text("Do it.")
        render = self.root / "render"
        render.mkdir(exist_ok=True)
        (render / "CLAUDE.md").write_text("Work.")
        (self.root / "logs").mkdir(exist_ok=True)
        return jb_cli_runner.Settings(
            task_ids=("acct/t1",),
            label=label,
            render_dir=render,
            tasks_base=self.root / "store",
            substrate=ClaudeSubstrate(),
            model="m",
            timeout_per_task="60",
            max_retries=max_retries,
            log_dir=self.root / "logs",
        )

    def roll(self, cfg, fake_cli):
        with patch.object(jb_opencode_runner, "TEMP_BASE", self.root / "ws"):
            return jb_opencode_runner.run_one(
                cfg, "acct/t1", stage=jb_cli_runner._stage, run=fake_cli
            )

    def test_cli_runner_stages_harness_root_and_keeps_deliverables(self):
        seen = []

        def fake_cli(cfg, workspace, traj_file, log_file, task_name, attempt):
            seen.extend(sorted(p.name for p in workspace.iterdir()))
            (workspace / "output/report.txt").write_text("done")
            return 0

        self.assertTrue(self.roll(self.runner_cfg(), fake_cli))
        self.assertEqual(seen, ["CLAUDE.md", "output", "task_folder"])
        self.assertTrue((self.root / "store/acct/t1/model_output/l1/report.txt").is_file())

    def test_crashed_attempt_never_delivers_its_partial_output(self):
        codes = iter([1, 0])  # a crash, then a clean run

        def fake_cli(cfg, workspace, traj_file, log_file, task_name, attempt):
            (workspace / f"output/attempt{attempt}.txt").write_text("x")
            return next(codes)

        self.assertTrue(self.roll(self.runner_cfg(max_retries=2), fake_cli))
        delivered = self.root / "store/acct/t1/model_output/l1"
        self.assertEqual([p.name for p in delivered.iterdir()], ["attempt2.txt"])
        partials = list((self.root / "store/acct/t1/model_traj/l1").glob("*.partial"))
        self.assertEqual([[p.name for p in d.iterdir()] for d in partials], [["attempt1.txt"]])

    def test_only_a_crash_withholds_partial_output_a_timeout_still_delivers(self):
        for code, delivered in ((1, False), (124, True)):
            with self.subTest(code=code):

                def fake_cli(cfg, workspace, traj_file, log_file, task_name, attempt):
                    (workspace / "output/partial.txt").write_text("x")
                    return code

                self.assertEqual(self.roll(self.runner_cfg(label=f"c{code}"), fake_cli), delivered)

    def test_stream_error_turns_a_zero_exit_into_a_failed_attempt(self):
        cases = [
            (PiSubstrate(), {"role": "assistant", "stopReason": "error"}, 1),
            (PiSubstrate(), {"role": "assistant", "stopReason": "stop"}, 0),
            (ClaudeSubstrate(), {"is_error": True}, 1),
            (ClaudeSubstrate(), {"is_error": False}, 0),
        ]
        for sub, final, expected in cases:
            event = (
                {"type": "message_end", "message": final}
                if sub.name == "pi"
                else {"type": "result", "subtype": "success", **final}
            )

            def fake_run(argv, **kw):
                kw["stdout"].write(json.dumps(event) + "\n")
                return SimpleNamespace(returncode=0)

            cfg = SimpleNamespace(substrate=sub, model="m", timeout_per_task="60")
            with self.subTest(sub=sub.name, final=final):
                with patch.object(jb_cli_runner.subprocess, "run", fake_run):
                    code = jb_cli_runner._attempt(
                        cfg, self.root, self.root / "t.jsonl", self.root / "log.txt", "t", 1
                    )
                self.assertEqual(code, expected)


if __name__ == "__main__":
    unittest.main()
