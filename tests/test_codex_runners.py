"""Run native launcher boundaries with a local fake CLI, never a model service."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import config
from harness2.benchmarks.workbuddy import WbAdapter
from harness2.cli import setup
from harness2.preflight import check


class NativeRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cli = self.root / "codex"
        self.cli.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if '--version' in sys.argv:\n"
            "    print('codex-cli fake'); sys.exit(0)\n"
            "if 'login' in sys.argv:\n"
            "    print('Logged in using ChatGPT'); sys.exit(0)\n"
            "if '--output-last-message' in sys.argv:\n"
            "    Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('done')\n"
            "workspace = Path.cwd()\n"
            "output = workspace / 'output'\n"
            "output.mkdir(exist_ok=True)\n"
            "(output / 'result.json').write_text(json.dumps({\n"
            "    'argv': sys.argv, 'home': os.environ.get('CODEX_HOME'),\n"
            "    'api_key_present': bool(os.environ.get('OPENAI_API_KEY'))}))\n"
            "print(json.dumps({'type': 'thread.started', 'thread_id': 'test-thread'}))\n"
            "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1}}))\n"
        )
        self.cli.chmod(0o755)
        self.home = self.root / "login"
        self.home.mkdir()
        self.auth = self.home / "auth.json"
        self.auth.write_text('preserve this sentinel without reading it\n')
        for name, value in (
            ("CODEX_AUTH_MODE", "chatgpt"), ("CODEX_BIN", str(self.cli)),
            ("CODEX_LOGIN_HOME", str(self.home)),
        ):
            override = patch.object(config, name, value)
            override.start()
            self.addCleanup(override.stop)

    def test_workbuddy_refuses_account_mode_before_setup_or_launch(self):
        with self.assertRaisesRegex(config.ConfigError, "WorkBuddy"):
            WbAdapter("code", substrate="cx")
        with patch("harness2.install.clone") as clone, self.assertRaises(SystemExit):
            setup.main(["wb", "--substrate", "cx"])
        clone.assert_not_called()

    def test_invalid_authentication_mode_is_rejected_before_downloads(self):
        with (
            patch.object(config, "CODEX_AUTH_MODE", "typo"),
            patch("harness2.install.clone") as clone,
            self.assertRaises(SystemExit),
        ):
            setup.main(["lab", "--substrate", "cx"])
        clone.assert_not_called()

    def test_readiness_does_not_require_unused_opencode(self):
        adapter = Mock()
        adapter.spec.name = "lab"
        adapter.substrate.name = "cx"
        with (
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "codex_cli"),
            patch.object(config, "validate_models"),
            patch("harness2.preflight.OpencodeSubstrate.preflight") as opencode,
            patch("subprocess.run", return_value=Mock(
                returncode=0, stdout="Logged in using ChatGPT", stderr=""
            )),
        ):
            errors = check(adapter)
        opencode.assert_not_called()
        self.assertFalse(any("OpenCode" in error or "opencode" in error for error in errors))

    def test_codex_improvement_checks_its_generated_provider_with_any_executor(self):
        adapter = Mock()
        adapter.spec.name = "lab"
        for executor in ("oc", "cx"):
            adapter.substrate.name = executor
            with (
                patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "codex_cli"),
                patch.object(config, "validate_models"),
                patch("harness2.preflight.OpencodeSubstrate.preflight"),
                patch("harness2.preflight.CodexSubstrate.preflight") as codex,
                patch("subprocess.run", return_value=Mock(returncode=0)),
            ):
                check(adapter)
            codex.assert_called_once_with(require_config=True)

    def test_account_setup_does_not_download_unused_opencode_or_codex(self):
        with (
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "codex_cli"),
            patch("harness2.install.clone") as clone,
            patch("harness2.install.patch"), patch("harness2.install.overlays"),
            patch("harness2.install.run") as run,
        ):
            self.assertEqual(setup.main(["lab", "--substrate", "cx", "--dry-run"]), 0)
        self.assertEqual([call.args[0] for call in clone.call_args_list], ["harvey-labs"])
        self.assertFalse(any(call.args[0][0] == "npm" for call in run.call_args_list))

    def test_api_setup_keeps_opencode_and_pinned_codex_installation(self):
        with (
            patch.object(config, "CODEX_AUTH_MODE", "api"),
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "opencode"),
            patch("harness2.install.clone") as clone,
            patch("harness2.install.patch"), patch("harness2.install.overlays"),
            patch("harness2.install.codex") as codex,
            patch("harness2.install.run") as run,
        ):
            self.assertEqual(setup.main(["lab", "--substrate", "cx", "--dry-run"]), 0)
        self.assertEqual([call.args[0] for call in clone.call_args_list], ["opencode", "harvey-labs"])
        codex.assert_called_once_with(dry_run=True)
        self.assertTrue(any(call.args[0][0] == "npm" for call in run.call_args_list))

    def test_api_codex_improvement_is_installed_with_an_opencode_executor(self):
        with (
            patch.object(config, "CODEX_AUTH_MODE", "api"),
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "codex_cli"),
            patch("harness2.install.clone"), patch("harness2.install.patch"),
            patch("harness2.install.overlays"), patch("harness2.install.run"),
            patch("harness2.install.codex") as codex,
        ):
            self.assertEqual(setup.main(["lab", "--substrate", "oc", "--dry-run"]), 0)
        codex.assert_called_once_with(dry_run=True)

    def test_relative_cli_and_login_paths_survive_session_directory_changes(self):
        from harness2.substrate.codex import CodexSubstrate

        workspace = self.root / "workspace"
        workspace.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with (
                patch.object(config, "CODEX_BIN", "./codex"),
                patch.object(config, "CODEX_LOGIN_HOME", "login"),
            ):
                substrate = CodexSubstrate()
                substrate.preflight()
                result = substrate.run_session(
                    "work", workspace, "test-model", self.root / "session", timeout=10,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(result, "done")
        artifact = json.loads((workspace / "output/result.json").read_text())
        self.assertEqual(artifact["home"], str(self.home))
        self.assertEqual(artifact["argv"][0], str(self.cli))

    def test_jobbench_shell_runner_uses_login_home_and_workspace_sandbox(self):
        task = self.root / "dataset/role/task1/task_folder"
        task.mkdir(parents=True)
        (task / "TASK_INSTRUCTIONS.txt").write_text("Write output/result.json.\n")
        env = {
            **os.environ,
            "HARNESS2_CODEX_AUTH_MODE": "chatgpt",
            "HARNESS2_CODEX_BIN": str(self.cli),
            "HARNESS2_CODEX_HOME": str(self.home),
            "CODEX_HOME": str(self.home),
            "OPENAI_API_KEY": "sentinel",
            "TASKS_BASE_DIR": str(self.root / "dataset"),
            "TASK_IDS": "role/task1", "BENCHMARK_MODELS": "test-model",
            "LABEL": "test", "TIMEOUT_PER_TASK": "10",
            "PYTHONPATH": str(config.ROOT),
        }
        runner = config.ROOT / "third_party/jb_runner/run_benchmark_codex_cli_adapt.sh"
        result = subprocess.run(
            ["bash", str(runner)], env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        artifact = json.loads(next(task.parent.glob("model_output/**/result.json")).read_text())
        self.assertEqual(artifact["home"], str(self.home))
        self.assertIn("workspace-write", artifact["argv"])
        self.assertFalse(artifact["api_key_present"])
        self.assertEqual(self.auth.read_text(), 'preserve this sentinel without reading it\n')

    def load_lab_runner(self, mode="chatgpt"):
        upstream = types.ModuleType("external.tts_lab.config")
        upstream.documents_dir = lambda task: self.root / "documents"
        upstream.task_json = lambda task: {"instructions": "Write output/result.json"}
        modules = {
            "external": types.ModuleType("external"),
            "external.tts_lab": types.ModuleType("external.tts_lab"),
            "external.tts_lab.config": upstream,
        }
        path = config.ROOT / "third_party/lab_runner/adapt_runs_cx/run_codex_lab.py"
        spec = importlib.util.spec_from_file_location("test_lab_codex_runner", path)
        runner = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules), patch.dict(os.environ, {
            "HARNESS2_CODEX_AUTH_MODE": mode, "ADAPT_LABCX_CODEX_BIN": str(self.cli),
        }):
            spec.loader.exec_module(runner)
        return runner

    def test_lab_runner_keeps_account_home_and_api_scratch_home_separate(self):
        for mode in ("chatgpt", "api"):
            with self.subTest(mode=mode):
                runner = self.load_lab_runner(mode)
                output = self.root / "results" / mode
                args = runner.parser.parse_args([
                    "--task", "task", "--run-id", mode, "--model", "test-model",
                    "--timeout", "10", "--doc-parser", "host",
                ])
                with (
                    patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
                    patch.object(runner, "SCRATCH_BASE", self.root / "scratch"),
                    patch.object(runner, "run_dir_for", return_value=output),
                    patch.object(runner, "install_harness", return_value=[]),
                    patch("signal.signal"),
                ):
                    self.assertEqual(runner.main(args), 0)
                artifact = json.loads((output / "output/result.json").read_text())
                if mode == "chatgpt":
                    self.assertEqual(artifact["home"], str(self.home))
                    self.assertIn("workspace-write", artifact["argv"])
                else:
                    self.assertIn("codex_home", artifact["home"])
                    self.assertFalse(Path(artifact["home"]).exists())
                    self.assertIn("--dangerously-bypass-approvals-and-sandbox", artifact["argv"])
        self.assertEqual(self.auth.read_text(), 'preserve this sentinel without reading it\n')

    def test_lab_reaper_preserves_authentication_inside_scratch(self):
        runner = self.load_lab_runner()
        scratch = self.root / "scratch"
        saved = scratch / "codex_home/saved-login"
        saved.mkdir(parents=True)
        (saved / "auth.json").write_text("sentinel")
        stale = scratch / "lab/stale"
        stale.mkdir(parents=True)
        with patch.object(runner, "SCRATCH_BASE", scratch), patch.dict(os.environ, {
            "CODEX_HOME": str(saved), "HARNESS2_CODEX_HOME": str(saved),
        }):
            self.assertEqual(runner.reap(0), 1)
        self.assertEqual((saved / "auth.json").read_text(), "sentinel")
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
