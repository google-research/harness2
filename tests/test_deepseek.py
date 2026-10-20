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

"""Contract tests, including the real pinned runtime against an unbilled local endpoint."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness2 import config
from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.benchmarks.lab import LabAdapter
from harness2.cli import _common, run_sweep
from harness2.cli import deepseek_demo as demo_cli
from harness2.core import context, prompts, recursion, runmark, schedule, tree
from harness2.core.ledger import Ledger
from harness2.deepseek_demo import FIXTURE_KEY, DeepSeekDemoAdapter, MessagesFixture
from harness2.demo import BASE_SCRIPT, DemoProposer
from harness2.substrate.base import ExecutionError, Substrate
from harness2.substrate.deepseek import (
    EVENTS,
    MANIFEST,
    DeepSeekSettings,
    DeepSeekSubstrate,
    render_events,
    validate_tree,
)

HAS_SDK = bool(importlib.util.find_spec("deepseek_harness")) and bool(
    importlib.util.find_spec("psutil")
)
SPEC = SimpleNamespace(install_mode="deepseek_profile")


class _DeepSeekCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_temp)
        self.root = Path(self.temp.name)
        self.substrate = DeepSeekSubstrate()

    def cleanup_temp(self):
        if os.name == "nt":
            # Upstream persists a readable cwd slug inside DSH_HOME, so its log
            # paths exceed legacy MAX_PATH even for short temporary workspaces.
            shutil.rmtree("\\\\?\\" + self.temp.name)
        self.temp.cleanup()

    def materialise(self, content=None, *, substrate=None):
        substrate = substrate or self.substrate
        source = self.root / f"source-{len(list(self.root.iterdir()))}"
        tree.write_tree(
            substrate.surface(),
            content or {"systemprompt.md": "Do the task."},
            source,
        )
        install = substrate.materialise(source, SPEC, {})
        self.addCleanup(shutil.rmtree, install.staging_dir)
        return install


class DeepSeekTests(_DeepSeekCase):
    def test_settings_and_model_configuration(self):
        with patch.dict(
            os.environ,
            {
                "HARNESS2_DEEPSEEK_MODEL": "fixed-model",
                "DEEPSEEK_BASE_URL": "http://localhost:9000/v1",
                "HARNESS2_DEEPSEEK_MAX_MODEL_CALLS": "7",
                "HARNESS2_DEEPSEEK_TEXT_TOOLS": "false",
            },
            clear=True,
        ):
            settings = DeepSeekSettings.from_env()
            self.assertEqual((settings.model, settings.max_model_calls), ("fixed-model", 7))
            self.assertEqual(settings.base_url, "http://localhost:9000/v1")
            self.assertFalse(settings.text_tools)
        invalid = [
            {"max_tokens": 0},
            {"max_model_calls": -1},
            {"shell_timeout": True},
            {"base_url": "file:///private"},
            {"base_url": "http://key:secret@host"},
            {"base_url": "https://host?api_key=secret"},
            {"base_url": "http://host:bad"},
            {"profile": "sdk"},
            {"provider": "openai"},
            {"reasoning_effort": "medium"},
            {"context_window": 20_000},
            {"api_key_env": "BAD=KEY"},
            {"model": ""},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(config.ConfigError):
                DeepSeekSettings(**values)
        with patch.dict(os.environ, {"HARNESS2_DEEPSEEK_MAX_TOKENS": "not-an-int"}):
            with self.assertRaisesRegex(config.ConfigError, "must be an integer"):
                DeepSeekSettings.from_env()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(config, "IMPROVER_MODEL", "provider/editor"),
            patch.object(config, "SOLVER_MODEL_OC", ""),
        ):
            config.validate_models(substrate="ds")
            with self.assertRaises(config.ConfigError):
                config.validate_models(substrate="oc")

    def test_protocol_surface_fingerprints_and_literal_components(self):
        self.assertIsInstance(self.substrate, Substrate)
        content = {
            "systemprompt.md": "Literal {{ missing }}. Use ledger skill.",
            "guardrails.md": "Guard marker",
            "LongTermMEMORY.md": "Memory marker",
            "tool_descriptions/tool_guidance.md": "Guidance marker",
            "skills/ledger/SKILL.md": "---\nname: ledger\ndescription: signed totals\n---\nBody.",
            "skills/ledger/scripts/helper.py": "print('helper')\n",
            "scripts/calculate.py": "print(42)\n",
            "plugins/extra.mjs": "export function apply(ctx) {}\n",
        }
        first = self.materialise(content)
        source = next(self.root.glob("source-*"))
        second = self.substrate.materialise(source, SPEC, {})
        self.addCleanup(shutil.rmtree, second.staging_dir)
        self.assertNotEqual(first.staging_dir, second.staging_dir)
        self.assertEqual(first.fingerprint, runmark.fingerprint(source))
        self.assertEqual(first.extras, second.extras)
        self.assertEqual(tree.read_tree(self.substrate.surface(), first.staging_dir), content)
        different = DeepSeekSubstrate(settings=replace(self.substrate.settings, model="another"))
        third = different.materialise(source, SPEC, {})
        self.addCleanup(shutil.rmtree, third.staging_dir)
        self.assertNotEqual(
            first.extras["execution_fingerprint"], third.extras["execution_fingerprint"]
        )
        native = json.loads(self.substrate._patch(first.staging_dir, self.root).read_text())
        inserted = next(row["insert"] for row in native if "insert" in row)
        self.assertIn("Literal {{ missing }}", inserted[0]["config"]["prompt"])
        self.assertTrue(any(row["name"] == "./_plugin_0.mjs" for row in inserted))
        self.assertIn("./plugins/extra.mjs", (first.staging_dir / "_plugin_0.mjs").read_text())
        server = next(row for row in native if row.get("id") == "sdk-jsonrpc-server")
        self.assertIn("harness2Plugin0Ready", server["inject"])
        self.assertTrue(
            next(row for row in native if row.get("id") == "session-log-deepseek")["disabled"]
        )
        for pack in ("adapt", "tts"):
            from harness2.core.spec import BenchPrompts

            text = prompts.propose(
                BenchPrompts(work="reconcile amounts", prompt_pack=pack),
                "INV",
                self.substrate.surface(),
            )
            self.assertIn("plugins/<name>.mjs", text)
            self.assertIn("NOT supported", text)

    def test_validation_is_actionable_not_silent(self):
        invalid = [
            {"sub_agents/check/AGENT.md": "---\ndescription: check\n---\nCheck."},
            {"plugin/legacy.ts": "export const Plugin = () => ({})"},
            {".codex/hooks.json": "{}"},
            {"cordis.patch.yml": "[]"},
            {"scripts/broken.py": "def invalid(:\n"},
            {"skills/no_name/SKILL.md": "Missing frontmatter"},
        ]
        for extra in invalid:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate_tree({"systemprompt.md": "Do the task.", **extra})
        install = self.materialise()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.substrate.materialise(
                install.staging_dir,
                SPEC,
                {"file:systemprompt.md": "replacement"},
            )
        with self.assertRaisesRegex(ValueError, "template slots"):
            self.substrate.materialise(install.staging_dir, SPEC, {"date": "today"})
        with self.assertRaisesRegex(ValueError, "install_mode"):
            self.substrate.materialise(
                install.staging_dir,
                SimpleNamespace(install_mode="codex_root"),
                {},
            )
        with self.assertRaisesRegex(RuntimeError, "benchmark launcher"):
            self.substrate.execute("task", install, ["one"], 1)

    def test_environment_does_not_inherit_other_credentials_or_homes(self):
        with patch.dict(
            os.environ,
            {
                "HARNESS2_JUDGE_API_KEY": "judge-secret",
                "OPENAI_API_KEY": "unrelated-secret",
                "DSH_HOME": "ambient-home",
                "DEEPSEEK_API_KEY": "executor-secret",
                "PROCESSOR_ARCHITECTURE": "AMD64",
            },
        ):
            env = self.substrate._env(self.root / "private-home")
            self.assertNotIn("HARNESS2_JUDGE_API_KEY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertEqual(env["DEEPSEEK_API_KEY"], "executor-secret")
            self.assertEqual(env["PROCESSOR_ARCHITECTURE"], "AMD64")
            self.assertEqual(env["DSH_HOME"], str(self.root / "private-home"))
            self.assertNotIn("executor-secret", json.dumps(self.substrate.runtime_identity()))
            dotenv = self.root / "private.env"
            dotenv.write_text("SECRET_FROM_DOTENV=must-not-load\n")
            with patch.dict(os.environ, env, clear=True):
                config.load_dotenv(dotenv)
                self.assertNotIn("SECRET_FROM_DOTENV", os.environ)

    def test_batch_dispatch_and_empty_result_fail_closed(self):
        def launch(request, install):
            self.assertEqual(len(request.run_ids), 1)
            return [self.root / request.run_ids[0]]

        substrate = DeepSeekSubstrate(launcher=launch)
        install = self.materialise(substrate=substrate)
        self.assertEqual(
            substrate.execute("task", install, ["a", "b"], 10),
            [self.root / "a", self.root / "b"],
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            substrate.execute("task", install, ["a", "a"], 10)
        worker = DeepSeekSubstrate(_worker_command=(sys.executable, "-c", "pass"))
        install = self.materialise(substrate=worker)
        with self.assertRaisesRegex(ExecutionError, "missing or invalid SDK result"):
            worker.run_workspace(
                install,
                run_dir=self.root / "missing-result",
                run_id="missing-result",
                prompt="Do not invent success",
                timeout=10,
            )

    def test_rendering_streams_failures_usage_and_truncation(self):
        events = [
            {
                "type": "assistant/message",
                "seq": 1,
                "data": {
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "consider signs"},
                            {"type": "text", "text": "Inspecting"},
                        ]
                    }
                },
                "usage": {"inputTokens": 10},
            },
            {
                "type": "tool/call",
                "seq": 2,
                "data": {
                    "name": "read",
                    "callId": "a",
                    "arguments": '{"file_path":"ledger.csv"}',
                },
            },
            {
                "type": "tool/result",
                "seq": 3,
                "data": {
                    "message": {"content": [{"type": "text", "text": "denied"}], "isError": True},
                    "error": {"code": "DENIED"},
                },
            },
            {"type": "turn/end", "seq": 4, "data": {"reason": {"kind": "error"}}},
            {"type": "turn/end", "data": {"reason": "malformed"}},
        ]
        stream = self.root / EVENTS
        stream.write_text(
            "\n".join(
                json.dumps(
                    {
                        "method": "session.event",
                        "params": {"sessionId": "one", "event": event},
                    }
                )
                for event in events + events[:1]
            )
            + "\n"
            + "\n".join(
                json.dumps(record)
                for record in (
                    {"method": "session.event", "params": None},
                    {"method": "session.event", "params": {"event": []}},
                    {"type": 123},
                )
            )
            + "\n{broken\n",
            encoding="utf-8",
        )
        text = render_events(stream, obs_trunc=1000, max_chars=10000)
        for token in ("[ACTION] read", "[OBSERVATION ERROR]", "[ERROR]", "[USAGE]", "Invalid"):
            self.assertIn(token, text)
        self.assertEqual(text.count("Inspecting"), 1)
        self.assertEqual(text.count("Invalid DeepSeek JSONL line"), 4)
        self.assertIn("render truncation", render_events(stream, obs_trunc=4, max_chars=80))

    def test_artifact_collection_failure_is_not_a_success(self):
        from harness2.substrate import deepseek

        code = (
            "import json,sys; from pathlib import Path; "
            "r=json.loads(Path(sys.argv[-1]).read_text()); "
            "Path(r['run_dir'],'metrics.json').write_text(json.dumps({"
            "'finish_reason':'completed','session_id':r['session_id']}))"
        )
        substrate = DeepSeekSubstrate(_worker_command=(sys.executable, "-c", code))
        install = self.materialise(substrate=substrate)
        copy = deepseek._safe_copy

        def fail_output(source, dest):
            if source.name == "output":
                raise ValueError("unsafe output")
            copy(source, dest)

        out = self.root / "artifact-failure"
        with patch.object(deepseek, "_safe_copy", side_effect=fail_output):
            with self.assertRaisesRegex(ExecutionError, "Cannot collect deliverables") as caught:
                substrate.run_workspace(
                    install,
                    run_dir=out,
                    run_id="artifact-failure",
                    prompt="work",
                    timeout=10,
                )
        self.assertEqual(caught.exception.run_dir, out)
        self.assertEqual(json.loads((out / "metrics.json").read_text())["state"], "failed")
        self.assertEqual(json.loads((out / MANIFEST).read_text())["state"], "failed")

    def test_cli_cells_and_run_identity(self):
        with patch.object(config, "RUNS", self.root):
            for bench in ("jb", "lab"):
                adapter = _common.make_adapter(bench, None, "ds")
                self.assertEqual(adapter.spec.install_mode, "deepseek_profile")
                self.assertFalse(adapter.spec.allow_subagents)
                self.assertTrue(
                    _common.summary_namespace(adapter, 1, seed=None).startswith(bench + "-ds")
                )
            with self.assertRaisesRegex(ValueError, "Harbor/Docker"):
                _common.make_adapter("wb", "office", "ds")
            adapter = _common.make_adapter("jb", None, "ds")
            args = SimpleNamespace(
                substrate="ds",
                split="all",
                max_tasks=1,
                only_domains="",
                seed=None,
            )
            with patch.object(run_sweep, "_stream_for", return_value=({}, [("domain", "task")])):
                run_sweep._check_identity(adapter, args, self.root / "identity")
                adapter.substrate.settings = replace(adapter.substrate.settings, max_model_calls=2)
                with self.assertRaisesRegex(ValueError, "different models or tasks"):
                    run_sweep._check_identity(adapter, args, self.root / "identity")

    @unittest.skipUnless(HAS_SDK, "install harness2[deepseek] for process tests")
    def test_timeout_cancellation_redaction_and_process_cleanup(self):
        import psutil

        code = (
            "import json, os, subprocess, sys, time; from pathlib import Path; "
            "r=json.loads(Path(sys.argv[-1]).read_text()); "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "Path(r['run_dir'],'child.pid').write_text(str(p.pid)); "
            "print(os.environ['DEEPSEEK_API_KEY'],flush=True); time.sleep(60)"
        )
        substrate = DeepSeekSubstrate(
            settings=DeepSeekSettings(shutdown_timeout=1),
            api_key="redact-this-key",
            _worker_command=(sys.executable, "-c", code),
        )
        install = self.materialise(substrate=substrate)
        for cancel in (False, True):
            out = self.root / ("cancel" if cancel else "timeout")
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    substrate.run_workspace,
                    install,
                    run_dir=out,
                    run_id=out.name,
                    prompt="work",
                    timeout=1 if not cancel else 30,
                )
                if cancel:
                    deadline = time.monotonic() + 5
                    while not (out / "child.pid").is_file() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(substrate.cancel(out.name))
                with self.assertRaises(ExecutionError) as caught:
                    future.result(timeout=15)
            self.assertNotIn("redact-this-key", str(caught.exception))
            self.assertNotIn("redact-this-key", (out / "worker.log").read_text())
            self.assertFalse(substrate.artifacts_intact(out))
            self.assertFalse((out / runmark.MARK).exists())
            self.assertFalse(psutil.pid_exists(int((out / "child.pid").read_text())))
            meta = json.loads((out / MANIFEST).read_text())
            self.assertEqual(meta["state"], "cancelled" if cancel else "timeout")


@unittest.skipUnless(HAS_SDK, "install harness2[deepseek] for real SDK contract tests")
class NativeDeepSeekTests(_DeepSeekCase):
    def fixture(self, respond=None):
        fixture = self.enterContext(MessagesFixture(respond=respond))
        settings = DeepSeekSettings(
            base_url=fixture.url,
            reasoning_effort="off",
            max_tokens=1024,
            max_model_calls=6,
        )
        return fixture, settings

    def test_native_candidate_behavior_isolation_and_cached_resume(self):
        fixture, settings = self.fixture()
        adapter = DeepSeekDemoAdapter(self.root, settings=settings, api_key=FIXTURE_KEY)
        baseline = adapter.execute("ledger-1", None, "baseline")
        changed = tree.read_tree(adapter.substrate.surface(), adapter.base_harness())
        changed["scripts/summarize.py"] = BASE_SCRIPT.replace(" if amount > 0", "")
        source = tree.write_tree(adapter.substrate.surface(), changed, self.root / "changed")
        candidate = adapter.execute("ledger-1", source, "candidate")
        self.assertEqual(adapter.deliverable_text(baseline), "Total: 105.25\n")
        self.assertEqual(adapter.deliverable_text(candidate), "Total: 85.15\n")
        old = json.loads((baseline / MANIFEST).read_text())
        new = json.loads((candidate / MANIFEST).read_text())
        for key in ("workspace", "home", "session_id"):
            self.assertNotEqual(old[key], new[key])
        count = len(fixture.requests)
        adapter.execute("ledger-1", source, "candidate")
        self.assertEqual(len(fixture.requests), count)
        (candidate / "output" / "answer.txt").write_text("tampered")
        adapter.execute("ledger-1", source, "candidate")
        self.assertEqual(len(fixture.requests), count + 3)
        self.assertEqual(adapter.deliverable_text(candidate), "Total: 85.15\n")
        self.assertTrue(adapter.artifacts_intact(candidate))
        self.assertEqual({r["model"] for r in fixture.requests}, {settings.model})
        self.assertTrue(
            all(
                "dsh_session_log" not in r and "dsh_plugin_packages" not in r
                for r in fixture.requests
            )
        )
        metrics = adapter.run_metrics(candidate)
        self.assertGreater(metrics["usage"]["inputTokens"], 0)
        self.assertIsNone(metrics["cost_usd"])

    def test_native_component_and_plugin_activation_and_continuation(self):
        fixture, settings = self.fixture(lambda request: {"type": "text", "text": "completed"})
        substrate = DeepSeekSubstrate(settings=settings, api_key=FIXTURE_KEY)
        install = self.materialise(
            {
                "systemprompt.md": "Literal {{ something }}; read ledger skill.",
                "guardrails.md": "GUARD_MARKER",
                "LongTermMEMORY.md": "MEMORY_MARKER",
                "tool_descriptions/tool_guidance.md": "TOOL_GUIDANCE_MARKER",
                "skills/ledger/SKILL.md": "---\nname: ledger\ndescription: ledger work\n---\nLAZY_BODY",
                "plugins/extra.mjs": (
                    'export const inject = ["systemPrompt"]; export function apply(ctx) {'
                    'ctx.effect(() => ctx.systemPrompt.section({name:"test:native",order:10,'
                    'text:"NATIVE_PLUGIN_MARKER",interpolate:false}));}'
                ),
            },
            substrate=substrate,
        )
        substrate.preflight()
        self.assertEqual(fixture.requests, [])
        out = substrate.run_workspace(
            install,
            run_dir=self.root / "native",
            run_id="native",
            prompt="First turn",
            timeout=60,
        )
        rendered = json.dumps(fixture.requests[0]["system"])
        for marker in (
            "{{ something }}",
            "GUARD_MARKER",
            "MEMORY_MARKER",
            "TOOL_GUIDANCE_MARKER",
            "ledger work",
            "NATIVE_PLUGIN_MARKER",
        ):
            self.assertIn(marker, rendered)
        self.assertNotIn("LAZY_BODY", rendered)
        first = json.loads((out / MANIFEST).read_text())
        substrate.continue_session(install, run_dir=out, prompt="Second turn", timeout=60)
        second = json.loads((out / MANIFEST).read_text())
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertIn("First turn", json.dumps(fixture.requests[-1]))
        self.assertIn("Second turn", json.dumps(fixture.requests[-1]))
        self.assertEqual(second["continuations"], 1)
        other = self.materialise({"systemprompt.md": "Different"}, substrate=substrate)
        with self.assertRaisesRegex(ValueError, "same harness"):
            substrate.continue_session(other, run_dir=out, prompt="No inheritance", timeout=60)

    def test_native_budget_failure_retains_behavioral_evidence(self):
        fixture, settings = self.fixture()
        adapter = DeepSeekDemoAdapter(
            self.root,
            settings=replace(settings, max_model_calls=1),
            api_key=FIXTURE_KEY,
        )
        rv = recursion._roll(
            adapter,
            Ledger(mode="agg", task_id="ledger-1", k=1),
            "ledger-1",
            "inv1",
            None,
            lambda label: label,
            self.root / "context",
        )
        self.assertFalse(rv.ok)
        self.assertTrue(rv.run_dir.is_dir())
        self.assertEqual(len(fixture.requests), 1)
        self.assertIn("HARNESS2_BUDGET", adapter.trajectory(rv.run_dir, obs_trunc=50000))
        context.build(self.root / "evidence", adapter, "ledger-1", [rv])
        text = (self.root / "evidence" / "rollouts" / "inv1.md").read_text()
        self.assertIn("Rollout incomplete", text)
        self.assertIn("[ACTION]", text)
        self.assertFalse((rv.run_dir / runmark.MARK).exists())

    def test_invalid_native_plugin_and_missing_session_fail_without_fallback(self):
        fixture, settings = self.fixture(lambda request: {"type": "text", "text": "done"})
        substrate = DeepSeekSubstrate(settings=settings, api_key=FIXTURE_KEY)
        broken = self.materialise(
            {
                "systemprompt.md": "Work.",
                "plugins/broken.mjs": "export function apply() { throw new Error('CANDIDATE_BOOT_ERROR'); }",
            },
            substrate=substrate,
        )
        out = self.root / "broken"
        with self.assertRaisesRegex(ExecutionError, "CANDIDATE_BOOT_ERROR"):
            substrate.run_workspace(
                broken,
                run_dir=out,
                run_id="repair",
                prompt="Work",
                timeout=30,
            )
        self.assertEqual(fixture.requests, [])
        self.assertFalse(substrate.artifacts_intact(out))
        failed = json.loads((out / MANIFEST).read_text())
        repaired = self.materialise(substrate=substrate)
        substrate.run_workspace(
            repaired,
            run_dir=out,
            run_id="repair",
            prompt="Work",
            timeout=30,
        )
        self.assertEqual(len(fixture.requests), 1)
        meta = json.loads((out / MANIFEST).read_text())
        self.assertNotEqual(meta["session_id"], failed["session_id"])
        home = Path(meta["home"])
        absent = home.with_name("saved-home")
        home.rename(absent)
        with self.assertRaisesRegex(ValueError, "missing DeepSeek home"):
            substrate.continue_session(repaired, run_dir=out, prompt="Continue", timeout=30)
        self.assertFalse(home.exists())
        self.assertEqual(len(fixture.requests), 1)

    def test_full_recursive_loop_reflection_evaluation_and_resume(self):
        fixture, settings = self.fixture()
        adapter = DeepSeekDemoAdapter(self.root, settings=settings, api_key=FIXTURE_KEY)
        proposer = DemoProposer(adapter.spec.channels["proposer"])
        (adapter.task_files("ledger-1") / "reference_answer.txt").write_text("GROUND_TRUTH")
        with patch.object(adapter, "score", side_effect=AssertionError("judge leaked")):
            result = schedule.run_domain(
                "accounting",
                adapter.tasks(),
                1,
                adapter=adapter,
                prop=proposer,
                runs_root=adapter.spec.runs_root,
                library_root=self.root / "library" / "k1",
            )
        self.assertEqual((result["done"], result["failed"]), (2, 0))
        self.assertEqual(len(fixture.requests), 42)
        states = [
            json.loads(path.read_text())["state"]
            for path in (self.root / "executions").rglob(MANIFEST)
        ]
        self.assertEqual(states.count("completed"), 14)
        for mode in ("agg", "seq"):
            work = adapter.spec.runs_root / f"deepseek-demo-ds-{mode}1"
            self.assertTrue(
                (
                    work / "ledger-2" / "context" / "inv1" / "library" / "harnesses" / "ledger-1"
                ).is_dir()
            )
            self.assertTrue(
                (
                    work
                    / "ledger-1"
                    / "reflection"
                    / f"{mode}1"
                    / "rollouts"
                    / ("agg1.md" if mode == "agg" else "c1.md")
                ).is_file()
            )
        self.assertNotIn("GROUND_TRUTH", json.dumps(fixture.requests))
        self.assertFalse((self.root / "evaluation.json").exists())
        calls = len(fixture.requests)
        resumed = schedule.run_domain(
            "accounting",
            adapter.tasks(),
            1,
            adapter=adapter,
            prop=proposer,
            runs_root=adapter.spec.runs_root,
            library_root=self.root / "library" / "k1",
        )
        self.assertEqual([row["status"] for row in resumed["tasks"]], ["skipped", "skipped"])
        self.assertEqual(len(fixture.requests), calls)
        evaluated = demo_cli.evaluate(adapter, 1, ("agg", "seq"))
        self.assertEqual([row["score"]["n_passed"] for row in evaluated["baseline"]], [0, 0])
        self.assertEqual([row["n_passed"] for row in evaluated["adapted"]], [1, 1, 1, 1])
        self.assertEqual(len(fixture.requests), calls)

    def test_jobbench_and_lab_native_paths_do_not_expose_criteria(self):
        def responder(request):
            text = json.dumps(request)
            system = request["system"]
            if isinstance(system, list):
                system = "\n".join(row.get("text", "") for row in system)
            bundle = Path(re.search(r"Harness directory: ([^\r\n]+)", system).group(1))
            if '"tool_use"' in text:
                return {"type": "text", "text": "done"}
            return {
                "type": "tool_use",
                "id": "call_write",
                "name": "write",
                "input": {
                    "file_path": str(bundle.parent / "workspace" / "output" / "answer.txt"),
                    "content": "Task delivered",
                },
            }

        import re

        fixture, settings = self.fixture(responder)
        repo = self.root / "dataset"
        jb_task = repo / "dataset" / "main" / "profession" / "task1"
        (jb_task / "task_folder").mkdir(parents=True)
        (jb_task / "task_folder" / "TASK_INSTRUCTIONS.txt").write_text("Write answer.txt")
        (jb_task / "RUBRICS.json").write_text('{"secret":"GROUND_TRUTH"}')
        lab_task = repo / "tasks" / "contracts" / "case"
        (lab_task / "documents").mkdir(parents=True)
        (lab_task / "documents" / "record.txt").write_text("A matter fact")
        (lab_task / "task.json").write_text(
            json.dumps(
                {
                    "title": "Task",
                    "instructions": "Write answer.txt",
                    "criteria": ["GROUND_TRUTH"],
                    "deliverables": {"answer.txt": "The work"},
                }
            )
        )
        with (
            patch.object(config, "RUNS", self.root / "runs"),
            patch.object(config, "jb_repo", return_value=repo),
            patch.object(config, "lab_repo", return_value=repo),
            patch.object(config, "JB_JUDGE_PYTHON", Path(sys.executable)),
            patch.object(config, "LAB_PYTHON", Path(sys.executable)),
        ):
            for adapter, task_id in (
                (JobBenchAdapter("ds"), "profession/task1"),
                (LabAdapter(substrate="ds"), "contracts/case"),
            ):
                adapter.substrate.settings = settings
                adapter.substrate._api_key_override = FIXTURE_KEY
                adapter.score = Mock(side_effect=AssertionError("No judging during execution"))
                out = adapter.execute(task_id, None, "native-benchmark", timeout=60)
                self.assertTrue(adapter.delivered(out))
                self.assertTrue(adapter.artifacts_intact(out))
                self.assertIn("[ACTION] write", adapter.trajectory(out, obs_trunc=50000))
                before = len(fixture.requests)
                adapter.execute(task_id, None, "native-benchmark", timeout=60)
                self.assertEqual(len(fixture.requests), before)
                adapter.score.assert_not_called()
        self.assertNotIn("GROUND_TRUTH", json.dumps(fixture.requests))


@unittest.skipUnless(
    HAS_SDK and os.environ.get("HARNESS2_DEEPSEEK_LIVE") == "1",
    "live endpoint smoke is opt-in: HARNESS2_DEEPSEEK_LIVE=1 (incurs API costs)",
)
class LiveDeepSeekTests(unittest.TestCase):
    def test_real_endpoint(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(demo_cli.main(["smoke", "--live", "--output", td]), 0)


if __name__ == "__main__":
    unittest.main()
