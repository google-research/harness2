"""Authentication and process-boundary regressions for optional Codex CLI sessions."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import config, install
from harness2.cli import demo as demo_cli
from harness2.cli import judge as judge_cli
from harness2.cli import run_sweep
from harness2.core.proposer import Proposer, ProposerError
from harness2.core.spec import ChannelSpec, resolve_channel_model
from harness2.substrate.codex import CodexSubstrate


class CodexLoginTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.substrate = CodexSubstrate()
        for name, value in (
            ("CODEX_AUTH_MODE", "chatgpt"),
            ("CODEX_BIN", "codex"),
            ("CODEX_LOGIN_HOME", ""),
        ):
            override = patch.object(config, name, value, create=True)
            override.start()
            self.addCleanup(override.stop)

    def test_api_mode_keeps_pinned_binary_generated_home_and_flags(self):
        with patch.object(config, "CODEX_AUTH_MODE", "api"):
            argv = self.substrate.exec_argv("task", self.root, "model")
            env = self.substrate.exec_env(
                {"CODEX_HOME": "/existing", "OPENAI_BASE_URL": "https://unused"},
                codex_home=self.root,
            )
        self.assertEqual(argv[0], str(Path(config.CODEX_DIST) / "codex"))
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertEqual(env["CODEX_HOME"], str(self.root))
        self.assertNotIn("OPENAI_BASE_URL", env)

    def test_chatgpt_uses_existing_login_home_without_api_credentials(self):
        original = {
            "CODEX_HOME": "/existing/login",
            "PATH": "/bin",
            "OPENAI_API_KEY": "sentinel",
            "CODEX_API_KEY": "sentinel",
            "OPENAI_BASE_URL": "https://unused",
        }
        env = self.substrate.exec_env(original, codex_home=self.root)
        self.assertEqual(env["CODEX_HOME"], "/existing/login")
        self.assertEqual(env["PATH"], "/bin")
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
            self.assertNotIn(key, env)
        self.assertIn("OPENAI_API_KEY", original)
        argv = self.substrate.exec_argv("task", self.root, "model")
        self.assertEqual(argv[0], shutil.which("codex") or "codex")
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("workspace-write", argv)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertIn("--ephemeral", argv)

    def test_chatgpt_home_override_does_not_create_a_directory(self):
        login_home = self.root / "saved-login"
        with patch.object(config, "CODEX_LOGIN_HOME", str(login_home)):
            env = self.substrate.exec_env({}, codex_home=self.root / "api")
        self.assertEqual(env["CODEX_HOME"], str(login_home))
        self.assertFalse(login_home.exists())

    def test_chatgpt_setup_and_preflight_require_saved_chatgpt_login(self):
        with (
            patch("shutil.which", return_value="/bin/codex"),
            patch("subprocess.run", return_value=Mock(
                returncode=0, stdout="Logged in using ChatGPT\n", stderr=""
            )) as run,
            patch.object(config, "CODEX_BASE_URL", ""),
            patch.object(config, "VERTEX_PROXY_KEY", ""),
        ):
            self.substrate.preflight()
            install.configure_codex()
            self.assertIsNone(install.codex_config_error())
        self.assertEqual(run.call_args.args[0], ["/bin/codex", "login", "status"])
        for result in (
            Mock(returncode=1, stdout="", stderr="Not logged in"),
            Mock(returncode=0, stdout="Logged in using an API key", stderr=""),
        ):
            with (
                patch("shutil.which", return_value="/bin/codex"),
                patch("subprocess.run", return_value=result),
                self.assertRaisesRegex(RuntimeError, "codex login"),
            ):
                self.substrate.preflight()

    def test_invalid_mode_and_missing_cli_fail_before_a_model_call(self):
        with (
            patch.object(config, "CODEX_AUTH_MODE", "typo"),
            self.assertRaisesRegex(config.ConfigError, "HARNESS2_CODEX_AUTH_MODE"),
        ):
            self.substrate.preflight()
        with (
            patch("shutil.which", return_value=None),
            self.assertRaisesRegex(RuntimeError, "Codex CLI"),
        ):
            self.substrate.preflight()

    def test_api_preflight_still_requires_endpoint_and_key(self):
        with (
            patch.object(config, "CODEX_AUTH_MODE", "api"),
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(config.ConfigError, "HARNESS2_CODEX_BASE_URL"),
        ):
            self.substrate.preflight()

    def test_api_improvement_rejects_a_stale_generated_provider_before_running(self):
        (self.root / "codex").touch()
        (self.root / "config.toml").write_text(
            f"model = {json.dumps(config.SOLVER_MODEL_CX)}\n"
            'model_provider = "harness2"\n'
            '[model_providers.harness2]\nbase_url = "https://old.example/v1"\n'
        )
        with (
            patch.object(config, "CODEX_AUTH_MODE", "api"),
            patch.object(config, "CODEX_DIST", self.root),
            patch.object(config, "CODEX_HOME_DIR", self.root),
            patch.object(config, "CODEX_BASE_URL", "https://new.example/v1"),
            patch.object(config, "require"),
            patch("socket.create_connection") as connect,
            patch("subprocess.run", return_value=Mock(
                returncode=0, stdout="", stderr=""
            )) as run,
        ):
            with self.assertRaisesRegex(RuntimeError, "stale"):
                self.substrate.preflight(require_config=True)
            with self.assertRaisesRegex(RuntimeError, "stale"):
                self.substrate.run_session(
                    "work", self.root, "test-model", self.root / "output", timeout=1,
                )
        connect.assert_not_called()
        run.assert_not_called()

    def test_api_improvement_uses_the_configured_provider_with_its_own_model(self):
        (self.root / "codex").touch()

        def execute(argv, **kwargs):
            self.assertEqual(argv[argv.index("--model") + 1], "improver-model")
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
            self.assertEqual(kwargs["env"]["CODEX_HOME"], str(self.root))
            self.assertEqual(kwargs["env"]["VERTEX_PROXY_KEY"], "sentinel")
            Path(argv[argv.index("--output-last-message") + 1]).write_text("done")
            return Mock(returncode=0, stdout='{"type":"turn.completed"}', stderr="")

        with (
            patch.object(config, "CODEX_AUTH_MODE", "api"),
            patch.object(config, "CODEX_DIST", self.root),
            patch.object(config, "CODEX_HOME_DIR", self.root),
            patch.object(config, "CODEX_BASE_URL", "https://configured.example/v1"),
            patch.object(config, "VERTEX_PROXY_KEY", "sentinel"),
            patch.object(config, "require"),
            patch("socket.create_connection"),
            patch("subprocess.run", side_effect=execute) as run,
        ):
            install.configure_codex()
            self.substrate.preflight(require_config=True)
            result = self.substrate.run_session(
                "work", self.root, "improver-model", self.root / "output", timeout=1,
            )
        self.assertEqual(result, "done")
        run.assert_called_once()


class CodexProposerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        mode = patch.object(config, "CODEX_AUTH_MODE", "chatgpt")
        mode.start()
        self.addCleanup(mode.stop)

    def make_proposer(self):
        return Proposer(ChannelSpec("codex_cli", "test-model"), audit_dir=self.root / "audit")

    def test_codex_channel_resolves_a_bare_model_without_changing_defaults(self):
        self.assertEqual(resolve_channel_model("codex_cli", "test-model").model, "test-model")
        self.assertEqual(config.DEFAULT_PROPOSER_CHANNEL, "opencode")
        with self.assertRaises(ValueError):
            resolve_channel_model("codex_cli", "provider/model")

    def test_success_harvests_edits_and_cached_calls_do_not_run_again(self):
        source = self.root / "source"
        (source / "harness").mkdir(parents=True)
        (source / "harness/AGENTS.md").write_text("before\n")
        visited = []

        def execute(argv, **kwargs):
            cwd = Path(kwargs["cwd"])
            visited.append(cwd)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            (cwd / "context/harness/AGENTS.md").write_text("after\n")
            answer = Path(argv[argv.index("--output-last-message") + 1])
            answer.write_text("Improved the instructions.\n")
            return Mock(returncode=0, stdout='{"type":"turn.completed"}\n', stderr="")

        proposer = self.make_proposer()
        with patch("subprocess.run", side_effect=execute) as run:
            reply = proposer.call("Improve this", source, "edit", harvest="context/harness")
            cached = proposer.call("Improve this", source, "edit", harvest="context/harness")
        self.assertEqual(run.call_count, 1)
        self.assertTrue(cached.cached)
        self.assertEqual((reply.workspace / "AGENTS.md").read_text(), "after\n")
        self.assertEqual((source / "harness/AGENTS.md").read_text(), "before\n")
        self.assertFalse(visited[0].exists())
        metadata = json.loads(reply.audit.with_name(reply.audit.name.replace(
            ".response.md", ".meta.json"
        )).read_text())
        self.assertEqual(metadata["channel"], "codex_cli")

    def test_nonzero_exit_rejects_partial_output_and_does_not_cache_success(self):
        proposer = self.make_proposer()

        def execute(argv, **kwargs):
            Path(argv[argv.index("--output-last-message") + 1]).write_text("partial")
            return Mock(returncode=1, stdout="", stderr="rate limit")

        with (
            patch("subprocess.run", side_effect=execute),
            self.assertRaisesRegex(ProposerError, "rate limit"),
        ):
            proposer.call("work", None, "failed", max_attempts=1)
        self.assertFalse(proposer._calls[-1].ok)

    def test_timeout_and_empty_output_do_not_become_successful_replies(self):
        proposer = self.make_proposer()
        for result in (
            subprocess.TimeoutExpired("codex", 1),
            Mock(returncode=0, stdout='{"type":"turn.completed"}', stderr=""),
        ):
            kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with patch("subprocess.run", **kwargs), self.assertRaises(ProposerError):
                proposer.call("work", None, "failed", max_attempts=1)

    def test_terminal_failure_rejects_a_written_final_answer(self):
        def execute(argv, **kwargs):
            Path(argv[argv.index("--output-last-message") + 1]).write_text("partial")
            return Mock(returncode=0, stdout=json.dumps({
                "type": "turn.failed", "error": {"message": "quota exhausted"}
            }), stderr="")

        with patch("subprocess.run", side_effect=execute), self.assertRaisesRegex(
            ProposerError, "quota exhausted"
        ):
            self.make_proposer().call("work", None, "failed", max_attempts=1)

    def test_cache_is_not_shared_between_models(self):
        def execute(argv, **kwargs):
            Path(argv[argv.index("--output-last-message") + 1]).write_text("done")
            return Mock(returncode=0, stdout='{"type":"turn.completed"}', stderr="")

        with patch("subprocess.run", side_effect=execute) as run:
            for model in ("model-one", "model-two"):
                proposer = Proposer(ChannelSpec("codex_cli", model), audit_dir=self.root / "audit")
                proposer.call("same task", None, "same-label")
        self.assertEqual(run.call_count, 2)

    def test_timeout_preserves_partial_trajectory_before_temporary_cleanup(self):
        proposer = self.make_proposer()
        partial = b'{"type":"turn.started"}\n'
        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired(
                "codex", 1, output=partial, stderr=b"waiting"
            )),
            self.assertRaisesRegex(ProposerError, "timeout"),
        ):
            proposer.call("work", None, "timeout", max_attempts=1)
        trajectories = list((self.root / "audit").glob("*.response.jsonl"))
        self.assertEqual(len(trajectories), 1)
        self.assertEqual(trajectories[0].read_bytes(), partial)


class CodexConfigurationTests(unittest.TestCase):
    def test_judge_accepts_codex_models_without_an_opencode_solver_model(self):
        with (
            patch.object(config, "SOLVER_MODEL_OC", ""),
            patch.object(config, "SOLVER_MODEL_CX", "test-model"),
            patch.object(config, "IMPROVER_MODEL", "test-model"),
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "codex_cli"),
            patch.dict(config.PROPOSER_MODEL_BY_CHANNEL, {"codex_cli": "test-model"}),
            patch.dict(os.environ, {"HARNESS2_JUDGE_MODEL": "test-judge"}),
            patch("harness2.cli.judge.C.make_adapter", side_effect=RuntimeError("models accepted")),
            self.assertRaisesRegex(RuntimeError, "models accepted"),
        ):
            judge_cli.main(["--bench", "lab", "--substrate", "cx"])

    def test_auth_change_invalidates_an_opencode_run_with_codex_improvement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = run_sweep._build_parser().parse_args(["--bench", "lab", "--substrate", "oc"])
            with (
                patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "codex_cli"),
                patch.object(config, "CODEX_AUTH_MODE", "chatgpt"),
                patch("harness2.cli.run_sweep._stream_for", return_value=({}, [])),
            ):
                run_sweep._check_identity(Mock(), args, root)
                with patch.object(config, "CODEX_AUTH_MODE", "api"), self.assertRaises(ValueError):
                    run_sweep._check_identity(Mock(), args, root)

    def test_api_endpoint_change_invalidates_an_opencode_run_with_codex_improvement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = run_sweep._build_parser().parse_args(["--bench", "lab", "--substrate", "oc"])
            with (
                patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "codex_cli"),
                patch.object(config, "CODEX_AUTH_MODE", "api"),
                patch.object(config, "CODEX_BASE_URL", "https://old.example/v1"),
                patch("harness2.cli.run_sweep._stream_for", return_value=({}, [])),
            ):
                run_sweep._check_identity(Mock(), args, root)
                with (
                    patch.object(config, "CODEX_BASE_URL", "https://new.example/v1"),
                    self.assertRaises(ValueError),
                ):
                    run_sweep._check_identity(Mock(), args, root)

    def test_offline_and_live_demos_cannot_share_artifact_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = root / "codex-config.json"
            record.write_text("{}")
            with self.assertRaises(SystemExit):
                demo_cli.main(["--output", str(root)])
            self.assertFalse((root / "base").exists())
            record.unlink()
            (root / "base").mkdir()
            with (
                patch.object(config, "CODEX_AUTH_MODE", "chatgpt"),
                patch.object(config, "SOLVER_MODEL_CX", "test-model"),
                self.assertRaises(SystemExit),
            ):
                demo_cli.main(["--output", str(root), "--substrate", "cx"])
            self.assertFalse(record.exists())


if __name__ == "__main__":
    unittest.main()
