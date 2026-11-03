# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""Claude Code substrate and JobBench runner regressions."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks import jb_claude_runner, jb_opencode_runner
from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.core import prompts, tree
from harness2.substrate.claude import CLAUDE_SURFACE, ClaudeSubstrate


class ClaudeSubstrateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _materialise(self, files: dict[str, str]) -> Path:
        source = tree.write_tree(CLAUDE_SURFACE, files, self.root / "source", stubs=True)
        install = ClaudeSubstrate().materialise(
            source, SimpleNamespace(install_mode="claude_root"), {}
        )
        self.addCleanup(shutil.rmtree, install.staging_dir, True)
        return install.staging_dir

    def test_materialise_maps_agents_and_limits_settings_to_hooks(self):
        hooks = {
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}
            ]
        }
        staged = self._materialise(
            {
                "CLAUDE.md": "Work carefully. Use the `pdf` skill for PDFs.\n",
                ".claude/skills/pdf/SKILL.md": (
                    "---\nname: pdf\ndescription: read PDFs\n---\nUse pdftotext.\n"
                ),
                "sub_agents/reviewer/AGENT.md": (
                    "---\ndescription: review work\n---\nReturn defects only.\n"
                ),
                ".claude/settings.json": json.dumps(
                    {"hooks": hooks, "permissions": {"allow": ["Bash(*)"]}, "env": {"X": "1"}}
                ),
            }
        )
        self.assertEqual(
            json.loads((staged / ".claude/settings.json").read_text()), {"hooks": hooks}
        )
        agent = (staged / ".claude/agents/reviewer.md").read_text()
        self.assertTrue(agent.startswith("---\nname: reviewer\ndescription: review work"))
        self.assertFalse((staged / "sub_agents").exists())

    def test_environment_isolated_and_uses_vertex(self):
        host = {
            "PATH": "/bin",
            "ANTHROPIC_API_KEY": "secret",
            "CLAUDE_CODE_OAUTH_TOKEN": "secret",
            "HARNESS2_JUDGE_API_KEY": "secret",
            "GITHUB_TOKEN": "secret",
        }
        with patch.object(config, "VERTEX_REGION", "us-east5"), patch.object(config, "CLAUDE_AUTH", "vertex"):
            env = ClaudeSubstrate().exec_env(host, config_dir=self.root)
        for name in host.keys() - {"PATH"}:
            self.assertNotIn(name, env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.root))
        self.assertEqual(env["CLAUDE_CODE_USE_VERTEX"], "1")
        self.assertEqual(env["CLOUD_ML_REGION"], "us-east5")

    def test_argv_is_noninteractive_ephemeral_and_project_scoped(self):
        with patch.object(config, "CLAUDE_BIN", Path("/opt/claude")):
            argv = ClaudeSubstrate().exec_argv("task", self.root, "claude-test")
        self.assertEqual(argv[0:3], ["/opt/claude", "-p", "task"])
        self.assertIn("stream-json", argv)
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")
        self.assertIn("--strict-mcp-config", argv)

    def test_login_preserves_credential_location_without_provider_override(self):
        host = {"HOME": "/user", "CLAUDE_CONFIG_DIR": "/custom/claude",
                "ANTHROPIC_API_KEY": "fake", "CLAUDE_CODE_USE_VERTEX": "1"}
        with patch.object(config, "CLAUDE_AUTH", "login"):
            env = ClaudeSubstrate().exec_env(host, config_dir=self.root)
        self.assertEqual(env["HOME"], "/user")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/custom/claude")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_USE_VERTEX", env)

    def test_renderer_and_terminal_failure_detection(self):
        stream = self.root / "events.jsonl"
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Working"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "content": "file.txt", "is_error": False}
                    ]
                },
            },
            {"type": "result", "subtype": "success", "is_error": False, "usage": {}},
        ]
        stream.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        substrate = ClaudeSubstrate()
        rendered = substrate.read_trajectory(stream, obs_trunc=10_000, max_chars=100_000)
        self.assertIn('[ACTION] Bash({"command": "ls"})', rendered)
        self.assertIn("[OBSERVATION] file.txt", rendered)
        self.assertFalse(substrate.run_failed(stream))
        stream.write_text(json.dumps({"type": "result", "subtype": "error", "is_error": True}))
        self.assertTrue(substrate.run_failed(stream))

    def test_prompt_surface_contains_only_claude_paths(self):
        surface = prompts._st(CLAUDE_SURFACE)
        for text in (surface.surface_md, surface.tts_surface, surface.inv, surface.dom):
            self.assertNotIn("AGENTS.md", text)
            self.assertNotIn(".agents/", text)
            self.assertNotIn(".codex/", text)
        self.assertIn("CLAUDE.md", surface.surface_md)
        self.assertIn(".claude/settings.json", surface.surface_md)

    def test_jobbench_dispatch_is_explicit(self):
        adapter = JobBenchAdapter(substrate="cc", store_root=self.root)
        self.assertIs(type(adapter.substrate), ClaudeSubstrate)
        self.assertTrue(adapter.spec.allow_subagents)
        self.assertEqual(adapter.spec.install_mode, "claude_root")
        with self.assertRaises(ValueError):
            JobBenchAdapter(substrate="unknown", store_root=self.root)

    def test_jobbench_runner_can_execute_as_a_script(self):
        result = subprocess.run(
            [sys.executable, str(Path(jb_claude_runner.__file__))],
            env={"PATH": config.os.environ.get("PATH", ""), "PYTHONPATH": str(config.ROOT)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotIn("ImportError", result.stderr)
        self.assertIn("unset:", result.stdout + result.stderr)

    def _runner_settings(self, *, retries: int = 1) -> jb_claude_runner.Settings:
        task = self.root / "store/accounting/task1/task_folder"
        task.mkdir(parents=True)
        (task / "TASK_INSTRUCTIONS.txt").write_text("Do it.\n")
        render = self.root / "render"
        render.mkdir()
        (render / "CLAUDE.md").write_text("Work.\n")
        logs = self.root / "logs"
        logs.mkdir()
        return jb_claude_runner.Settings(
            task_ids=("accounting/task1",),
            label="test",
            render_dir=render,
            tasks_base=self.root / "store",
            model="claude-test",
            timeout_per_task="60",
            max_retries=retries,
            log_dir=logs,
            substrate=ClaudeSubstrate(),
        )

    def test_failed_partial_output_is_quarantined_before_retry(self):
        codes = iter((1, 0))

        def fake_run(cfg, workspace, trajectory, log, task, attempt):
            (workspace / "output" / f"attempt{attempt}.txt").write_text("partial")
            return next(codes)

        cfg = self._runner_settings(retries=2)
        with patch.object(jb_opencode_runner, "TEMP_BASE", self.root / "workspaces"):
            self.assertTrue(
                jb_opencode_runner.run_one(
                    cfg,
                    "accounting/task1",
                    stage=jb_claude_runner._stage,
                    run=fake_run,
                )
            )
        delivered = self.root / "store/accounting/task1/model_output/test"
        self.assertEqual([path.name for path in delivered.iterdir()], ["attempt2.txt"])
        partial = self.root / "logs/partials/test/accounting_task1/attempt1"
        self.assertEqual([path.name for path in partial.iterdir()], ["attempt1.txt"])


if __name__ == "__main__":
    unittest.main()
