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

"""Focused regressions for the paper's execution, evidence, and adoption contracts."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import config
from harness2.core import context, judge, recursion, schedule, tree
from harness2.core.library import Library
from harness2.core.proposer import Proposer, ProposerError, Reply
from harness2.core.spec import resolve_channel_model
from harness2.demo import BASE_SCRIPT, DemoAdapter, DemoProposer


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.proposer = DemoProposer(self.adapter.spec.channels["proposer"])

    def run_stream(self, k=2, proposer=None):
        return schedule.run_domain(
            "accounting",
            self.adapter.tasks(),
            k,
            adapter=self.adapter,
            prop=proposer or self.proposer,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "library" / str(k),
        )

    def test_recursion_budget_and_resume(self):
        with patch.dict(config.PROPOSER_MODEL_BY_CHANNEL, {"opencode": "", "claude_cli": ""}):
            unresolved = resolve_channel_model(None, None)
            self.assertEqual(unresolved.model, "")
            with self.assertRaises(ValueError):
                Proposer(unresolved)
            with self.assertRaises(ValueError):
                resolve_channel_model("opencode", "")
        for k in (1, 2):
            with self.subTest(k=k):
                result = self.run_stream(k)
                self.assertEqual((result["done"], result["failed"]), (2, 0))
                for task_id in self.adapter.tasks():
                    summary = json.loads(
                        (
                            self.adapter.spec.runs_root
                            / f"demo-oc-agg{k}"
                            / task_id
                            / "summary.json"
                        ).read_text()
                    )
                    for mode in ("agg", "seq"):
                        ledger = summary["modes"][mode]["ledger"]
                        self.assertEqual((ledger["R"], ledger["P"]), (4 * k, 3 * k))
                        selection = summary["selections"][mode]
                        expected = "Total: 85.15\n" if task_id == "ledger-1" else "Total: 20.25\n"
                        self.assertEqual(
                            self.adapter.deliverable_text(Path(selection["chosen_run_dir"])),
                            expected,
                        )
                executions, calls = self.adapter.execution_count, len(self.proposer._calls)
                resumed = self.run_stream(k)
                self.assertEqual(
                    (self.adapter.execution_count, len(self.proposer._calls)), (executions, calls)
                )
                self.assertEqual(
                    [row["status"] for row in resumed["tasks"]], ["skipped", "skipped"]
                )
        self.assertFalse(
            any(
                reply.label.endswith(".agg1") or reply.label.endswith(".seq1")
                for reply in self.proposer._calls
                if reply.label.startswith("select.")
            )
        )
        work = self.adapter.spec.runs_root / "demo-oc-seq2" / "ledger-1"
        summary_path = self.adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json"
        chosen = json.loads(summary_path.read_text())["selections"]["seq"]
        (work / "harnesses" / chosen["chosen_label"] / "scripts/summarize.py").write_text(
            "changed after selection\n"
        )
        repaired = self.run_stream()
        self.assertEqual([row["status"] for row in repaired["tasks"]], ["ok", "skipped"])
        chosen = json.loads(summary_path.read_text())["selections"]["agg"]
        (Path(chosen["chosen_run_dir"]) / "answer.txt").unlink()
        repaired = self.run_stream()
        self.assertEqual([row["status"] for row in repaired["tasks"]], ["ok", "skipped"])

    def test_complete_evidence_and_library_prefix(self):
        (self.adapter.task_files("ledger-1") / "reference_answer.txt").write_text('"pass_rate": 1')
        self.run_stream()
        work = self.adapter.spec.runs_root / "demo-oc-seq2" / "ledger-1"
        reflection = work / "reflection/seq2"
        required = [
            "source/ledger.csv",
            "rollouts/base1.md",
            "rollouts/c2.md",
            "pool/inv1/harness.diff",
            "decisions/c1.md",
            "adoption.json",
            "base_harness/scripts/summarize.py",
            "harness/scripts/summarize.py",
        ]
        self.assertEqual([name for name in required if not (reflection / name).is_file()], [])
        self.assertTrue((work / "context/inv2/pool/c1/rollout.md").is_file())
        self.assertEqual(list(work.rglob("reference_answer.txt")), [])
        next_context = work.parent / "ledger-2/context/inv1/library/harnesses"
        self.assertEqual([path.name for path in next_context.iterdir()], ["ledger-1"])
        with self.assertRaises(ValueError):
            context.assert_tree_has_no_grader_verdicts({"EXPERIENCE.md": '"pass_rate": 1'})
        previous_sessions = []

        def run_opencode(command, **kwargs):
            database = Path(kwargs["env"]["XDG_DATA_HOME"]) / "opencode.db"
            previous_sessions.append(database.exists())
            database.parent.mkdir(parents=True, exist_ok=True)
            database.write_text("private evidence from this session")
            return Mock(stdout='{"type":"text","part":{"text":"done"}}', stderr="", returncode=0)

        proposer = Proposer(self.proposer.spec)
        with (
            patch("harness2.core.proposer._opencode_dir", return_value=self.root),
            patch("harness2.core.proposer.subprocess.run", side_effect=run_opencode),
        ):
            for index in range(2):
                proposer.call("Read this task only", None, f"isolation{index}")
        self.assertEqual(previous_sessions, [False, False])

    def test_invalid_proposal_retries_with_feedback_and_accumulates_cost(self):
        class RepairProposer(DemoProposer):
            feedback = ""

            def call(self, mission, context_dir, label, **kwargs):
                script = Path(context_dir) / "harness/scripts/summarize.py"
                if label.endswith(".a1"):
                    script.write_text("def broken(:\n")
                    return Reply(
                        label,
                        "invalid edit",
                        True,
                        cost_usd=1,
                        attempts=1,
                        workspace=script.parent.parent,
                    )
                self.feedback = mission
                script.write_text(BASE_SCRIPT)
                reply = super().call(mission, context_dir, label, **kwargs)
                reply.cost_usd = 2
                return reply

        proposer = RepairProposer(self.proposer.spec)
        stage = recursion._run_stage(
            label="inv1",
            dim="INV",
            job="Repair the script",
            adapter=self.adapter,
            prop=proposer,
            ctx_dir=self.root / "context",
            task_id="ledger-1",
            rollouts=[],
            fallback=tree.read_tree(self.adapter.substrate.surface(), self.adapter.base_harness()),
            harness_dir=self.adapter.base_harness(),
            library_dir=None,
        )
        self.assertEqual((stage.fell_back, stage.attempts, stage.usd), (False, 2, 3))
        self.assertIn("PREVIOUS ATTEMPT WAS REJECTED", proposer.feedback)

    def test_no_entry_stays_out_of_library_on_resume(self):
        class NoExperience(DemoProposer):
            def call(self, mission, context_dir, label, **kwargs):
                if label.startswith("experience."):
                    if label.endswith("seq1"):
                        raise ProposerError("reflection failed", attempts=2, cost_usd=2)
                    return Reply(label, "NO_ENTRY", True, cost_usd=1)
                return super().call(mission, context_dir, label, **kwargs)

        proposer = NoExperience(self.proposer.spec)
        self.run_stream(k=1, proposer=proposer)
        self.run_stream(k=1, proposer=proposer)
        for mode in ("agg", "seq"):
            library = Library(
                self.root / "library/1" / mode, "accounting", self.adapter.substrate.surface()
            )
            self.assertEqual(library.entries(), [])
        summary_path = self.adapter.spec.runs_root / "demo-oc-agg1/ledger-1/summary.json"
        selections = json.loads(summary_path.read_text())["selections"]
        self.assertEqual([selections[mode]["usd"] for mode in ("agg", "seq")], [1, 2])

    def test_failed_selection_is_never_judged(self):
        class FailedSelector(DemoProposer):
            def call(self, mission, context_dir, label, **kwargs):
                if label.startswith("select."):
                    raise ProposerError("selection failed", attempts=2, cost_usd=3)
                return super().call(mission, context_dir, label, **kwargs)

        self.run_stream(proposer=FailedSelector(self.proposer.spec))
        summary_path = self.adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json"
        summary = json.loads(summary_path.read_text())
        self.assertEqual([summary["selections"][mode]["usd"] for mode in ("agg", "seq")], [3, 3])
        self.adapter.score = Mock(return_value={"n_passed": 1, "n_criteria": 1})
        rows = judge.judge_selections(
            self.adapter,
            list(summary["selections"].values()),
        )
        self.adapter.score.assert_not_called()
        self.assertEqual((rows[0].graded, rows[0].n_passed), (False, None))

    def test_scrub_env_across_launchers(self):
        from harness2.benchmarks import jobbench, lab, workbuddy
        from harness2.substrate.base import Install, RunRequest
        from harness2.substrate.codex import CodexSubstrate

        cx = CodexSubstrate()
        prop_inst = Proposer(self.proposer.spec, audit_dir=self.root / "audit_env")
        host = {
            "HARNESS2_JUDGE_API_KEY": "secret-judge",
            "HARNESS2_BRIDGE_KEY": "secret-bridge",
            "HARNESS2_CODEX_API_KEY": "secret-codex",
            "OPENAI_API_KEY": "sk-judge",
            "ANTHROPIC_API_KEY": "sk-ant",
            "ANTHROPIC_AUTH_TOKEN": "tok-ant",
            "ANTHROPIC_BASE_URL": "https://proxy.example/v1",
            "GITHUB_TOKEN": "ghp_secret",
            "AWS_ACCESS_KEY_ID": "AKIA_TEST",
            "AWS_SECRET_ACCESS_KEY": "aws_secret",
            "AWS_SESSION_TOKEN": "aws_sess",
            "GOOGLE_APPLICATION_CREDENTIALS": "/adc.json",
        }
        with patch.dict("os.environ", host):
            for env_map in (
                config.scrub_env(),
                prop_inst.env(),
                self.adapter.substrate.run_env(config.os.environ, self.root),
                cx.exec_env(config.os.environ, codex_home=self.root),
                lab._lab_env(),
            ):
                for key in (
                    "HARNESS2_JUDGE_API_KEY",
                    "HARNESS2_BRIDGE_KEY",
                    "HARNESS2_CODEX_API_KEY",
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN",
                    "ANTHROPIC_BASE_URL",
                    "GITHUB_TOKEN",
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                    "AWS_SESSION_TOKEN",
                ):
                    self.assertNotIn(key, env_map)
                self.assertEqual(env_map["GOOGLE_APPLICATION_CREDENTIALS"], "/adc.json")
            judge_env = lab._lab_env(judge=True)
            self.assertEqual(judge_env["OPENAI_API_KEY"], "sk-judge")
            self.assertEqual(judge_env["ANTHROPIC_API_KEY"], "sk-ant")
            self.assertEqual(judge_env["ANTHROPIC_AUTH_TOKEN"], "tok-ant")
            self.assertEqual(judge_env["ANTHROPIC_BASE_URL"], "https://proxy.example/v1")
            self.assertNotIn("GITHUB_TOKEN", judge_env)
            self.assertNotIn("AWS_ACCESS_KEY_ID", judge_env)
            self.assertNotIn("HARNESS2_CODEX_API_KEY", judge_env)

            with patch("harness2.demo.subprocess.run") as demo_run:
                demo_run.return_value = Mock(stdout="42\n", stderr="")
                self.adapter.execute("ledger-1", self.adapter.base_harness(), "env_check", timeout=5)
                demo_env = demo_run.call_args.kwargs["env"]
                self.assertNotIn("HARNESS2_JUDGE_API_KEY", demo_env)
                self.assertNotIn("GITHUB_TOKEN", demo_env)

            jb = jobbench.JobBenchAdapter.__new__(jobbench.JobBenchAdapter)
            jb.substrate = Mock()
            jb.store = self.root / "jb_store"
            jb.domain_of = lambda _tid: "finance"
            jb._require_artifacts = lambda _tid, _lbl: self.root / "jb_out"
            captured = {}
            jb._run_runner = lambda _r, env, **_kw: captured.update(env)
            with patch("harness2.benchmarks.jobbench.render_opencode_tree"):
                jb._launch_opencode(
                    RunRequest(task_ref="t1", run_ids=("r1",), timeout=10),
                    Install(staging_dir=self.root, fingerprint="fp"),
                )
            self.assertNotIn("HARNESS2_JUDGE_API_KEY", captured)
            self.assertNotIn("GITHUB_TOKEN", captured)

            jb_repo_dir = self.root / "jb_repo"
            (jb_repo_dir / "eval").mkdir(parents=True)
            (jb_repo_dir / "eval" / "judge.py").write_text("# stub\n")
            with (
                patch("harness2.config.jb_repo", return_value=jb_repo_dir),
                patch("harness2.benchmarks.jobbench.subprocess.run") as jb_extract_run,
            ):
                jb_extract_run.return_value = Mock(returncode=0, stdout='{"files": {}}')
                jb._extract(self.root / "jb_deliverable")
                extract_env = jb_extract_run.call_args.kwargs["env"]
                self.assertNotIn("HARNESS2_JUDGE_API_KEY", extract_env)
                self.assertNotIn("GITHUB_TOKEN", extract_env)

            wb_jobs = self.root / "wb_repo" / "configs" / "jobs"
            wb_jobs.mkdir(parents=True)
            proc_mock = Mock(returncode=0)
            proc_mock.communicate.return_value = ("", "")
            with (
                patch("harness2.config.wb_repo", return_value=self.root / "wb_repo"),
                patch("harness2.benchmarks.workbuddy._job_yaml", return_value="job: test\n"),
                patch("harness2.benchmarks.workbuddy.subprocess.Popen", return_value=proc_mock) as popen,
            ):
                workbuddy.run_batch(
                    subset=next(iter(workbuddy.SUBSETS.values())),
                    substrate=next(iter(workbuddy._slots)),
                    label="b1",
                    tasks=["t1"],
                    ws=self.root,
                    k=1,
                    label_dir=self.root / "wb_runs",
                    done=lambda _t: False,
                    timeout_s=5,
                )
                wb_env = popen.call_args.kwargs["env"]
                self.assertNotIn("HARNESS2_JUDGE_API_KEY", wb_env)
                self.assertNotIn("GITHUB_TOKEN", wb_env)

    def test_jobbench_opencode_runner_runs_as_script(self):
        import subprocess
        import sys

        from harness2.benchmarks import jobbench

        res = subprocess.run(
            [sys.executable, str(jobbench.OC_RUNNER)],
            env={"PATH": config.os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotIn("ImportError", res.stderr)
        self.assertIn("unset:", res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
