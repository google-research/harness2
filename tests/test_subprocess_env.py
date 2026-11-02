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

"""Regressions for keeping runner and host secrets out of agent subprocesses."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import config
from harness2.core.proposer import Proposer
from harness2.demo import DemoAdapter, DemoProposer


class SubprocessEnvTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.proposer = DemoProposer(self.adapter.spec.channels["proposer"])

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
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok",
            "GOOGLE_API_KEY": "g-key",
            "GOOGLE_GENERATIVE_AI_API_KEY": "g-genai-key",
            "GOOGLE_APPLICATION_CREDENTIALS": "/adc.json",
        }
        # A Vertex solver keeps no provider key, whatever the developer's .env configures.
        with (
            patch.dict("os.environ", host),
            patch.object(config, "SOLVER_MODEL_OC", "google-vertex/gemini-test"),
        ):
            launchers = {
                "scrub_env": config.scrub_env(),
                "proposer": prop_inst.env(),
                "opencode": self.adapter.substrate.run_env(config.os.environ, self.root),
                "codex": cx.exec_env(config.os.environ, codex_home=self.root),
                "lab": lab._lab_env(),
            }
            for name, env_map in launchers.items():
                with self.subTest(launcher=name):
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
                        "AWS_BEARER_TOKEN_BEDROCK",
                        "GOOGLE_API_KEY",
                        "GOOGLE_GENERATIVE_AI_API_KEY",
                    ):
                        self.assertNotIn(key, set(env_map))
                    self.assertEqual(env_map["GOOGLE_APPLICATION_CREDENTIALS"], "/adc.json")
            # The LAB judge keeps only the keys its --judge-model's client reads.
            judge_cases = {
                "vertex/claude-test": set(),
                "vertex/gemini-test": set(),
                "claude-test": {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"},
                "gemini-test": {"GOOGLE_API_KEY"},
                "gpt-test": {"OPENAI_API_KEY"},
                "mistral-test": set(),
            }
            for model, kept in judge_cases.items():
                with self.subTest(judge=model):
                    judge_env = lab._lab_env(keep=lab._judge_keys(model))
                    scrubbed = set(host) - {"GOOGLE_APPLICATION_CREDENTIALS"}
                    self.assertEqual(scrubbed & set(judge_env), kept)

            with patch("harness2.demo.subprocess.run") as demo_run:
                demo_run.return_value = Mock(stdout="42\n", stderr="")
                self.adapter.execute(
                    "ledger-1", self.adapter.base_harness(), "env_check", timeout=5
                )
                demo_env = demo_run.call_args.kwargs["env"]
                self.assertNotIn("HARNESS2_JUDGE_API_KEY", set(demo_env))
                self.assertNotIn("GITHUB_TOKEN", set(demo_env))

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
            self.assertNotIn("HARNESS2_JUDGE_API_KEY", set(captured))
            self.assertNotIn("GITHUB_TOKEN", set(captured))

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
                self.assertNotIn("HARNESS2_JUDGE_API_KEY", set(extract_env))
                self.assertNotIn("GITHUB_TOKEN", set(extract_env))

            (self.root / "RUBRICS.json").write_text("{}\n")
            judge_python = self.root / "judge_python"
            judge_python.write_text("")
            jb._dataset_task = lambda _tid: self.root
            with (
                patch("harness2.config.jb_repo", return_value=jb_repo_dir),
                patch.object(config, "JB_JUDGE_PYTHON", judge_python),
                patch.object(config, "JB_JUDGE_API_KEY", "secret-judge"),
                patch("harness2.benchmarks.jobbench.subprocess.run") as jb_judge_run,
            ):
                jb_judge_run.return_value = Mock(returncode=0, stderr="")
                jb._run_judge("finance/task1", "r1", self.root / "jb_out")
                jb_judge_env = jb_judge_run.call_args.kwargs["env"]
                self.assertEqual(jb_judge_env["JUDGE_API_KEY"], "secret-judge")
                self.assertNotIn("HARNESS2_JUDGE_API_KEY", set(jb_judge_env))
                self.assertNotIn("GITHUB_TOKEN", set(jb_judge_env))

            wb_jobs = self.root / "wb_repo" / "configs" / "jobs"
            wb_jobs.mkdir(parents=True)
            proc_mock = Mock(returncode=0)
            proc_mock.communicate.return_value = ("", "")
            with (
                patch("harness2.config.wb_repo", return_value=self.root / "wb_repo"),
                patch("harness2.benchmarks.workbuddy._job_yaml", return_value="job: test\n"),
                patch(
                    "harness2.benchmarks.workbuddy.subprocess.Popen", return_value=proc_mock
                ) as popen,
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
                self.assertNotIn("HARNESS2_JUDGE_API_KEY", set(wb_env))
                self.assertNotIn("GITHUB_TOKEN", set(wb_env))

    def test_lab_launchers_keep_only_their_own_keys(self):
        from harness2.benchmarks import lab
        from harness2.substrate.base import Install, RunRequest

        host = {
            "OPENAI_API_KEY": "sk-openai",
            "ANTHROPIC_API_KEY": "sk-ant",
            "ANTHROPIC_BASE_URL": "https://proxy.example/v1",
            "HARNESS2_CODEX_API_KEY": "secret-codex",
            "HARNESS2_JUDGE_API_KEY": "secret-judge",
            "GITHUB_TOKEN": "ghp_secret",
        }
        adapter = lab.LabAdapter.__new__(lab.LabAdapter)
        adapter._results_root = self.root / "lab_results"
        adapter.solver_effort = "high"
        adapter.skill_mode = "native"
        req = RunRequest(task_ref="t1", run_ids=("r1",), timeout=10)
        install = Install(staging_dir=self.root, fingerprint="fp")
        run_dir = self.root / "lab_run"
        run_dir.mkdir()
        cases = {
            "oc anthropic/claude-test": (
                "_launch_oc",
                "anthropic/claude-test",
                {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"},
            ),
            "oc google-vertex/gemini-test": ("_launch_oc", "google-vertex/gemini-test", set()),
            "cx": ("_launch_cx", "gpt-test", set()),
            "judge vertex/claude-test": ("score", "vertex/claude-test", set()),
            "judge gpt-test": ("score", "gpt-test", {"OPENAI_API_KEY"}),
        }
        with (
            patch.dict("os.environ", host),
            patch.object(config, "VERTEX_PROXY_KEY", "secret-codex"),
            patch.object(lab, "_require_runner"),
            patch.object(lab, "run_detached") as launch,
        ):
            for name, (method, model, kept) in cases.items():
                with self.subTest(launcher=name):
                    adapter.model = adapter.judge_model = model
                    if method == "score":
                        adapter.score(run_dir, "t1")
                    else:
                        getattr(adapter, method)(req, install)
                    env_map = launch.call_args.kwargs["env"]
                    self.assertEqual(set(host) & set(env_map), kept)
                    if method == "_launch_cx":
                        self.assertEqual(env_map["VERTEX_PROXY_KEY"], "secret-codex")

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

    def test_configured_provider_keeps_its_key(self):
        from dataclasses import replace

        from harness2.substrate.opencode import OpencodeSubstrate

        host = {
            "OPENAI_API_KEY": "sk-openai",
            "ANTHROPIC_API_KEY": "sk-ant",
            "ANTHROPIC_BASE_URL": "https://proxy.example/v1",
            "GEMINI_API_KEY": "gm-key",
            "GOOGLE_API_KEY": "g-key",
            "GOOGLE_GENERATIVE_AI_API_KEY": "g-genai-key",
            "AWS_ACCESS_KEY_ID": "AKIA_TEST",
            "AWS_SECRET_ACCESS_KEY": "aws_secret",
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok",
            "GITHUB_TOKEN": "ghp_secret",
            "HARNESS2_JUDGE_API_KEY": "secret-judge",
        }
        cases = {
            "openai/gpt-test": {"OPENAI_API_KEY"},
            "anthropic/claude-test": {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"},
            "google/gemini-test": {
                "GOOGLE_API_KEY",
                "GOOGLE_GENERATIVE_AI_API_KEY",
                "GEMINI_API_KEY",
            },
            "amazon-bedrock/claude-test": {
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_BEARER_TOKEN_BEDROCK",
            },
            "google-vertex/gemini-test": set(),
        }
        with patch.dict("os.environ", host):
            for model, kept in cases.items():
                with self.subTest(model=model):
                    with patch.object(config, "SOLVER_MODEL_OC", model):
                        executor = OpencodeSubstrate().run_env(config.os.environ, self.root)
                    explicit = OpencodeSubstrate().run_env(
                        config.os.environ, self.root, model=model
                    )
                    improver = Proposer(
                        replace(self.proposer.spec, kind="opencode", model=model),
                        audit_dir=self.root / "audit_keep",
                    ).env()
                    for env_map in (executor, explicit, improver):
                        self.assertEqual(set(host) & set(env_map), kept)


if __name__ == "__main__":
    unittest.main()
