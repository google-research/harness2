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

"""Regressions at render, launch, and whole-task resume boundaries (no model calls)."""

import asyncio
import dataclasses
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from harness2 import config, install
from harness2.benchmarks import jb_opencode_runner, jobbench, lab, workbuddy
from harness2.core import policy, recursion, schedule, tree
from harness2.demo import DemoAdapter, DemoProposer
from harness2.substrate.codex import CODEX_SURFACE, CodexSubstrate

ROOT = Path(__file__).resolve().parents[1]
LITERAL = "Keep {{ forbidden_host }} secret. <!-- Require approval. --> {% literal %} {# text #}"


def package(name, path=None):
    module = types.ModuleType(name)
    module.__path__ = [str(path)] if path else []
    return module


@contextmanager
def lab_runtime(root):
    """Supply only external LAB data; load the shipped renderer and installer unchanged."""
    harness_run = types.ModuleType("harness.run")
    harness_run.DEFAULT_SKILLS = []
    harness_run.SKILLS_DIR = root
    harness_run.SYSTEM_PROMPT_PREAMBLE = "Stock LAB prompt."
    harness_run.load_skills = lambda names: ""
    tts_config = types.ModuleType("external.tts_lab.config")
    modules = {
        "harness": package("harness"),
        "harness.run": harness_run,
        "opencode": package("opencode", ROOT / "third_party/lab_runner/opencode"),
        "opencode.pinned_policy": policy,
        "external": package("external"),
        "external.tts_lab": package("external.tts_lab"),
        "external.tts_lab.config": tts_config,
    }
    with patch.dict(sys.modules, modules):
        renderer = importlib.import_module("opencode.render")
        spec = importlib.util.spec_from_file_location(
            "policy_lab_codex", ROOT / "third_party/lab_runner/adapt_runs_cx/run_codex_lab.py"
        )
        codex = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(codex)
        codex.READ_DOC = ROOT / "third_party/lab_runner/opencode/scripts/read_doc.py"
        yield renderer, codex


@contextmanager
def wb_runtime():
    """Load actual agents with Harbor's external process/model interfaces stubbed."""
    modules = {"harbor": package("harbor")}
    imports = {
        "harbor.agents.installed.base": "BaseInstalledAgent",
        "harbor.environments.base": "BaseEnvironment",
        "harbor.models.agent.context": "AgentContext",
        "harbor.models.trajectories.agent": "Agent",
        "harbor.models.trajectories.final_metrics": "FinalMetrics",
        "harbor.models.trajectories.metrics": "Metrics",
        "harbor.models.trajectories.step": "Step",
        "harbor.models.trajectories.tool_call": "ToolCall",
        "harbor.models.trajectories.trajectory": "Trajectory",
    }
    base = type("BaseInstalledAgent", (), {"render_instruction": lambda self, value: value})
    for name, symbol in imports.items():
        for i in range(1, len(name.split(".")) + 1):
            parent = ".".join(name.split(".")[:i])
            modules.setdefault(parent, package(parent))
        setattr(modules[name], symbol, base if symbol == "BaseInstalledAgent" else object)
    modules["workbuddy_bench"] = package("workbuddy_bench")
    modules["workbuddy_bench.agents"] = package(
        "workbuddy_bench.agents", ROOT / "third_party/wb_runner/agents"
    )
    modules["workbuddy_bench.agents.pinned_policy"] = policy
    user = types.ModuleType("workbuddy_bench.agents._agent_user")
    user.ensure_agent_user = AsyncMock()
    modules[user.__name__] = user
    with patch.dict(sys.modules, modules):
        oc = importlib.import_module("workbuddy_bench.agents.oc_agent")
        cx = importlib.import_module("workbuddy_bench.agents.codex_harness_agent")
        yield oc, cx, base


class PolicyExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)

    def test_jobbench_renders_before_literal_policy_injection(self):
        staged = self.adapter.base_harness()
        (staged / "systemprompt.md").write_text("Editable {{ date }}. <!-- omit -->")
        out = self.root / "rendered"
        jobbench.render_opencode_tree(staged, out, {"date": "today"}, pinned_policy=LITERAL)
        actual = (out / "AGENTS.md").read_text()
        self.assertTrue(actual.startswith(policy.block(LITERAL)))
        self.assertIn("Editable today.", actual)
        self.assertNotIn("<!-- omit -->", actual)

    def test_jobbench_checks_installed_file_before_launch(self):
        workspace = self.root / "ws"
        workspace.mkdir()
        (workspace / "AGENTS.md").write_text("tampered")
        with (
            patch.dict(
                os.environ,
                {
                    policy.ENV: LITERAL,
                    "HARNESS2_POLICY_MODULE": str(Path(policy.__file__).resolve()),
                },
            ),
            patch.object(jb_opencode_runner.subprocess, "run") as launch,
        ):
            with self.assertRaisesRegex(ValueError, "pinned policy missing"):
                jb_opencode_runner._attempt(
                    Mock(), workspace, self.root / "traj", self.root / "log", "task", 1
                )
            launch.assert_not_called()

    def test_codex_shell_policy_runtime_preserves_literal_bytes_and_refuses_oversize(self):
        ws = self.root / "ws"
        ws.mkdir()
        target = ws / "AGENTS.md"
        target.write_text("Stock body.")
        env = {**os.environ, policy.ENV: LITERAL}
        result = subprocess.run(
            [sys.executable, policy.__file__, str(ws)], env=env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text(), policy.block(LITERAL) + "Stock body.")
        before = target.read_bytes()
        env[policy.ENV] = "x" * 40000
        result = subprocess.run(
            [sys.executable, policy.__file__, str(ws)], env=env, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_bytes(), before)

    def test_direct_materialisation_and_ctx_overrides_obey_policy_budget(self):
        spec = dataclasses.replace(self.adapter.spec, pinned_policy="x" * 24000)
        with self.assertRaisesRegex(ValueError, "over the"):
            self.adapter.substrate.materialise(self.adapter.base_harness(), spec, {})
        spec = dataclasses.replace(self.adapter.spec, pinned_policy=LITERAL)
        with self.assertRaisesRegex(ValueError, "over the"):
            self.adapter.substrate.materialise(
                self.adapter.base_harness(), spec, {"file:systemprompt.md": "x" * 24000}
            )
        source = tree.write_tree(CODEX_SURFACE, {"AGENTS.md": "Work."}, self.root / "cx")
        spec = dataclasses.replace(spec, install_mode="codex_root", pinned_policy="x" * 40000)
        with self.assertRaisesRegex(ValueError, "over the"):
            CodexSubstrate().materialise(source, spec, {})

    def test_byte_limit_is_checked_separately_and_whitespace_policy_is_noop(self):
        with self.assertRaisesRegex(ValueError, "UTF-8 byte"):
            policy.prepend("", "😀" * 100, max_chars=1000, max_bytes=300)
        self.assertEqual(policy.prepend("body", " \n\t", max_chars=0), "body")
        self.assertEqual(policy.digest(" \n\t"), "")

    def test_autotrigger_reserves_policy_budget(self):
        adapter = self.adapter
        adapter.spec = dataclasses.replace(adapter.spec, pinned_policy=LITERAL)
        surface = adapter.substrate.surface()
        proposed = tree.read_tree(surface, adapter.base_harness())
        proposed["skills/helper/SKILL.md"] = "---\nname: helper\ndescription: do work\n---\nSteps."
        cap = surface.always_on_max - policy.reserved_chars(LITERAL)
        proposed["systemprompt.md"] = "x" * (cap - 6)
        _, error = recursion._accept_tree(surface, adapter, proposed, dict(proposed), "test")
        self.assertNotIn("over the", error)
        self.assertNotIn("load the `helper`", proposed["guardrails.md"])

    def test_lab_base_and_candidate_keep_rendered_body_with_literal_policy(self):
        with lab_runtime(self.root) as (renderer, _):
            for candidate in (None, self.adapter.base_harness()):
                for mode in ("append", "replace"):
                    with self.subTest(candidate=candidate, mode=mode):
                        ws = self.root / f"lab-{candidate is None}-{mode}"
                        original = renderer.render_tree(
                            ws, candidate, base_skill_names=[], prompt_mode=mode
                        )
                        before = original.prompt
                        renderer.apply_pinned_policy(ws, original, LITERAL)
                        self.assertEqual(original.prompt, policy.block(LITERAL) + before)
                        target = (
                            ws / "AGENTS.md" if mode == "append" else ws / ".opencode/agent/lab.md"
                        )
                        self.assertIn(original.prompt, target.read_text())

    def test_lab_codex_base_and_candidate_inject_after_install(self):
        with lab_runtime(self.root) as (_, cx), patch.dict(os.environ, {policy.ENV: LITERAL}):
            source = tree.write_tree(
                CODEX_SURFACE, {"AGENTS.md": "Existing base."}, self.root / "cx"
            )
            for candidate in (None, source):
                ws = self.root / f"cx-{candidate is None}"
                ws.mkdir()
                cx.install_harness(ws, candidate)
                expected = "" if candidate is None else "Existing base."
                self.assertEqual((ws / "AGENTS.md").read_text(), policy.block(LITERAL) + expected)

    def test_lab_and_workbuddy_base_installs_preserve_base_semantics(self):
        for cls in (lab.LabAdapter, workbuddy.WbAdapter):
            adapter = object.__new__(cls)
            adapter.spec = dataclasses.replace(self.adapter.spec, pinned_policy=LITERAL)
            adapter.base_harness = Mock(return_value=self.adapter.base_harness())
            adapter.substrate = Mock()
            result = adapter._install(None)
            self.assertEqual(result.extras["pinned_policy"], LITERAL)
            self.assertTrue(result.fingerprint.startswith("base-"))
            adapter.substrate.materialise.assert_not_called()
            if cls is workbuddy.WbAdapter:
                self.assertEqual(result.extras["install_mode"], "none")
                with patch.object(shutil, "rmtree") as remove:
                    adapter._drop_staging(result)
                    remove.assert_not_called()
            else:
                self.assertEqual(result.extras["harness"], "base")

    def test_workbuddy_policy_is_prepended_after_template_render_for_base_and_candidate(self):
        with wb_runtime() as (oc, _, base):
            agent = object.__new__(oc.OcAgent)
            agent._pinned_policy = LITERAL
            for rendered in ("Task text.", "Editable instructions.\nTask text."):
                with patch.object(base, "render_instruction", return_value=rendered) as render:
                    actual = agent.render_instruction("Task text.")
                    render.assert_called_once_with("Task text.")
                    self.assertEqual(actual, policy.block(LITERAL) + rendered)
            agent._pinned_policy = ""
            self.assertEqual(agent.render_instruction("Task text."), "Task text.")

    def test_workbuddy_codex_installs_then_injects_without_replacing_base_instructions(self):
        with wb_runtime() as (_, cx, _):
            source = tree.write_tree(CODEX_SURFACE, {"AGENTS.md": "Candidate."}, self.root / "cx")
            for candidate in (None, source):
                agent = object.__new__(cx.CodexHarnessAgent)
                agent._harness_ws = candidate
                agent._pinned_policy = LITERAL + " $(touch should-not-exist) `literal` 'quote'"
                ws = self.root / f"container-{candidate is None}"
                ws.mkdir()
                (ws / "AGENTS.md").write_text("Task image instructions.")

                async def execute(environment, *, command):
                    result = subprocess.run(
                        ["bash", "-o", "pipefail", "-c", command.replace("/workspace", str(ws))],
                        capture_output=True,
                        text=True,
                    )
                    return types.SimpleNamespace(return_code=result.returncode)

                agent.exec_as_agent = AsyncMock(side_effect=execute)
                asyncio.run(agent._materialize_harness(Mock()))
                self.assertEqual(
                    (ws / "AGENTS.md").read_text(),
                    policy.block(agent._pinned_policy)
                    + ("Candidate." if candidate else "Task image instructions."),
                )
                self.assertFalse((ws / "should-not-exist").exists())
                agent._pinned_policy = "x" * 40000
                with patch.object(cx.CodexAgent, "run", new_callable=AsyncMock) as launch:
                    with self.assertRaisesRegex(RuntimeError, "verification failed"):
                        asyncio.run(agent.run("Task", Mock(), Mock()))
                    launch.assert_not_called()
            self.assertEqual((source / "AGENTS.md").read_text(), "Candidate.")

    def test_workbuddy_job_passes_policy_to_base_and_candidate_agents(self):
        for substrate in ("oc", "cx"):
            for ws in (None, self.adapter.base_harness()):
                text = workbuddy._job_yaml(
                    workbuddy.SUBSETS["office"],
                    substrate,
                    "test",
                    ["task"],
                    ws,
                    1,
                    self.root,
                    pinned_policy=LITERAL,
                )
                self.assertIn(f"{policy.ENV}: {json.dumps(LITERAL)}", text)

    def test_setup_copies_one_policy_runtime_into_both_isolated_checkouts(self):
        with patch.object(config, "DEPENDENCIES", self.root / "deps"):
            for name, relative in (
                ("harvey-labs", "opencode/pinned_policy.py"),
                ("workbuddy-bench", "src/workbuddy_bench/agents/pinned_policy.py"),
            ):
                install.overlays(name)
                for substrate in ("oc", "cx"):
                    install.require_policy_runtime(name, substrate)
                self.assertEqual(
                    (config.DEPENDENCIES / name / relative).read_bytes(),
                    Path(policy.__file__).read_bytes(),
                )

    def test_old_runner_is_rejected_before_a_configured_policy_can_be_ignored(self):
        with patch.object(config, "DEPENDENCIES", self.root / "deps"):
            with self.assertRaisesRegex(RuntimeError, "rerun harness2-setup"):
                install.require_policy_runtime("harvey-labs", "oc")
            install.overlays("harvey-labs")
            runner = config.DEPENDENCIES / "harvey-labs/opencode/run_opencode.py"
            runner.write_text("# old runner that ignores the policy environment")
            with self.assertRaisesRegex(RuntimeError, "outdated"):
                install.require_policy_runtime("harvey-labs", "oc")

    def test_policy_changes_and_removal_invalidate_whole_task_resume(self):
        adapter = self.adapter
        proposer = DemoProposer(adapter.spec.channels["proposer"])
        kwargs = dict(
            adapter=adapter,
            prop=proposer,
            runs_root=adapter.spec.runs_root,
            library_root=self.root / "library",
        )
        for pinned in ("", LITERAL, "Different policy.", ""):
            adapter.spec = dataclasses.replace(adapter.spec, pinned_policy=pinned)
            result = schedule.run_domain("accounting", ["ledger-1"], 1, **kwargs)
            self.assertEqual([row["status"] for row in result["tasks"]], ["ok"])
            before = adapter.execution_count
            result = schedule.run_domain("accounting", ["ledger-1"], 1, **kwargs)
            self.assertEqual([row["status"] for row in result["tasks"]], ["skipped"])
            self.assertEqual(adapter.execution_count, before)

    def test_summary_never_merges_modes_from_a_different_policy(self):
        summary = self.root / "summary.json"
        old = schedule.TaskResult("task", "domain", 1, policy_digest=policy.digest("old"))
        old.modes = {"seq": {"ok": True}}
        old.selections = {"seq": {"ok": True, "chosen_label": "old"}}
        schedule._write_summary(summary, old)
        new = schedule.TaskResult("task", "domain", 1, policy_digest=policy.digest("new"))
        new.modes = {"agg": {"ok": True}}
        new.selections = {"agg": {"ok": True, "chosen_label": "new"}}
        schedule._write_summary(summary, new)
        self.assertEqual(set(json.loads(summary.read_text())["modes"]), {"agg"})


if __name__ == "__main__":
    unittest.main()
