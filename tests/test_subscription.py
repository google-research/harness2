"""Regressions for native auth, fresh processes, and failed result handling."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness2 import config, subscription
from harness2.benchmarks.lab_subscription import SubscriptionLabAdapter
from harness2.cli import judge as judge_cli
from harness2.core.judge import judge_one
from harness2.core.proposer import Proposer
from harness2.core.spec import ChannelSpec
from harness2.subscription_judge import CliJudge
from harness2.substrate.base import Install
from harness2.substrate.codex import CodexSubstrate


class SubscriptionTests(unittest.TestCase):
    def test_subscription_experiment_cannot_be_judged_with_api_transport(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ns = root / "experiment"
            ns.mkdir()
            (ns / "config.json").write_text(
                json.dumps(
                    {
                        "transport": "subscription",
                        "judge": config.JUDGE_MODEL,
                    }
                )
            )
            with (
                patch.object(config, "SUBSCRIPTION", False),
                patch.object(config, "TRANSPORT", "api"),
                patch.object(config, "validate_models"),
                patch.object(judge_cli.C, "make_adapter"),
                patch.object(judge_cli.C, "runs_root_for", return_value=root),
                patch.object(judge_cli.C, "summary_namespace", return_value="experiment"),
                patch.object(judge_cli, "collect_selections", return_value=[]) as collect,
            ):
                with self.assertRaisesRegex(SystemExit, "transport changed"):
                    judge_cli.main(["--bench", "lab", "--substrate", "cx", "--k", "1"])
                collect.assert_not_called()

    def test_unfinished_reference_is_not_judged_as_zero(self):
        with tempfile.TemporaryDirectory() as td:
            adapter = object.__new__(SubscriptionLabAdapter)
            with patch("harness2.benchmarks.lab_subscription.run_detached") as launch:
                self.assertIsNone(adapter.score(Path(td), "example/task"))
                launch.assert_not_called()

    def test_failed_native_rollout_never_gets_completion_marker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adapter = object.__new__(SubscriptionLabAdapter)
            adapter._results_root = root / "results"
            adapter._deliverables = {}
            adapter.model, adapter.solver_effort, adapter.timeout = "test-model", "medium", 10
            adapter.runner = Mock()
            adapter.runner.install_harness.return_value = []
            adapter.runner.build_prompt.return_value = "task"
            adapter.task_files = Mock(return_value=None)
            adapter._install = Mock(return_value=Install(root, "base", {"harness": "base"}))
            adapter.substrate = CodexSubstrate(launcher=adapter._launch_cx)
            with patch(
                "harness2.benchmarks.lab_subscription.invoke",
                side_effect=RuntimeError("broken transport"),
            ):
                with self.assertRaisesRegex(RuntimeError, "broken transport"):
                    adapter.execute("example/task", None, "failure")
            out = root / "results/failure"
            self.assertTrue((out / "config.json").exists())
            self.assertFalse((out / "metrics.json").exists())
            self.assertFalse((out / ".adapt_complete").exists())

    def test_invalid_verdict_remains_ungraded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "rubric_criterion.txt").write_text("Evaluate the supplied output.")
            judge = CliJudge("claude_cli", "test-model", root)
            adapter = SimpleNamespace(
                score=lambda *args: judge.evaluate_from_file("rubric_criterion", {})
            )
            with (
                patch.dict("sys.modules", {"evaluation.judge": SimpleNamespace(PROMPTS_DIR=root)}),
                patch.object(judge, "ask", return_value={}),
            ):
                result = judge_one(adapter, "task", "agg", 1, "agg1", "run", root)
            self.assertFalse(result.graded)
            self.assertIsNone(result.n_criteria)
            self.assertIn("Malformed verdict", result.error)

    def test_api_overrides_cannot_change_subscription_route(self):
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "test-paid",
                "OPENAI_API_KEY": "test-paid",
                "CLAUDE_CODE_USE_VERTEX": "1",
                "ANTHROPIC_BASE_URL": "https://wrong.invalid",
                "CODEX_HOME": "/retained/auth/home",
                "CLAUDE_CONFIG_DIR": "/retained/claude",
            },
            clear=True,
        ):
            env = subscription.environment()
        self.assertEqual(
            env, {"CODEX_HOME": "/retained/auth/home", "CLAUDE_CONFIG_DIR": "/retained/claude"}
        )

    def test_claude_verbose_result_and_error(self):
        payload = [{"type": "system"}, {"type": "result", "subtype": "success", "result": "done"}]
        self.assertEqual(subscription.claude_result(json.dumps(payload))["result"], "done")
        payload[-1]["is_error"] = True
        with self.assertRaises(RuntimeError):
            subscription.claude_result(json.dumps(payload))

    def test_codex_partial_or_failed_stream_is_not_success(self):
        started = json.dumps({"type": "thread.started", "thread_id": "new"})
        for tail in (
            "",
            '\n{"type":"turn.failed"}',
            '\n{"type":"turn.completed"}\n{"type":"turn.failed"}',
        ):
            with self.subTest(tail=tail), self.assertRaises(RuntimeError):
                subscription.codex_result(started + tail)
        result = subscription.codex_result(started + '\n{"type":"turn.completed","usage":{}}')
        self.assertEqual(result["session_id"], "new")

    def test_nonzero_cli_exit_is_rejected_even_with_success_payload(self):
        def failed_process(cmd, **kwargs):
            kwargs["stdout"].write('{"subtype":"success","result":"misleading"}')
            return Mock(returncode=1)

        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(subscription.subprocess, "Popen", side_effect=failed_process),
        ):
            root = Path(td)
            with self.assertRaises(RuntimeError):
                subscription.invoke(
                    "claude_cli", "test-model", "mission", workspace=root, audit=root / "audit"
                )
            self.assertFalse((root / "audit/result.json").exists())

    def test_proposer_uses_fresh_native_context_and_harvests_edits(self):
        seen = []

        def native(channel, model, prompt, *, workspace, **kwargs):
            self.assertEqual(channel, "claude_cli")
            target = workspace / "context/harness/AGENTS.md"
            self.assertEqual(target.read_text(), "original")
            seen.append(workspace)
            target.write_text("improved")
            return {"result": "done"}

        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(config, "SUBSCRIPTION", True),
            patch.object(subscription, "invoke", side_effect=native),
        ):
            root = Path(td)
            (root / "context/harness").mkdir(parents=True)
            (root / "context/harness/AGENTS.md").write_text("original")
            proposer = Proposer(ChannelSpec("claude_cli", "test-model"), audit_dir=root / "audit")
            for label in ("first", "second"):
                reply = proposer.call("edit", root / "context", label, harvest="context/harness")
                self.assertEqual((reply.workspace / "AGENTS.md").read_text(), "improved")
            self.assertNotEqual(*seen)
            self.assertEqual((root / "context/harness/AGENTS.md").read_text(), "original")

    def test_codex_improver_does_not_fall_through_to_opencode(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(config, "SUBSCRIPTION", True),
            patch.object(subscription, "invoke", return_value={"result": "done"}) as invoke,
        ):
            prop = Proposer(ChannelSpec("codex_cli", "test-model"), audit_dir=Path(td))
            prop.call("mission", None, "codex")
            self.assertEqual(invoke.call_args.args[0], "codex_cli")

    def test_matching_failure_cannot_become_missing_deliverable(self):
        judge = CliJudge("claude_cli", "test-model", Path("unused"))
        # The helper imports LAB readers. Inject only that dependency for this unit test.
        fake = Mock(_read_file_as_text=lambda p: "text")
        with (
            patch.dict("sys.modules", {"evaluation.scoring": fake}),
            patch.object(judge, "ask", return_value={"wanted": "invented.docx"}),
        ):
            with tempfile.TemporaryDirectory() as td, self.assertRaises(ValueError):
                judge.match_deliverables({"wanted": "wanted.docx"}, ["actual.docx"], Path(td))


if __name__ == "__main__":
    unittest.main()
