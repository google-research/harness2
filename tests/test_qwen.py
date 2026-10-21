"""Qwen executor contracts: native files, protocol, real process lifecycle and CLI."""

import contextlib
import io
import json
import os
import shlex
import signal
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks import jb_qwen_runner as runner
from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.cli import _common, run_sweep, setup
from harness2.core import prompts, runmark, schedule, tree
from harness2.demo import BASE_SCRIPT, DemoProposer
from harness2.substrate import qwen


def message(kind, content, iid="m1"):
    return {"type": kind, "uuid": iid, "message": {"id": iid, "content": content}}


def call(iid="c1", name="write_file", **args):
    return {"type": "tool_use", "id": iid, "name": name, "input": args}


def observation(iid="c1", **kwargs):
    return {"type": "tool_result", "tool_use_id": iid, "content": "done", **kwargs}


SUCCESS = {"type": "result", "subtype": "success", "is_error": False}
FAKE_CLI = """
import json, os, pathlib, signal, subprocess, sys, time
mode = sys.argv[1]
root = pathlib.Path.cwd()
prompt = sys.stdin.read()
(root / 'output/result.txt').write_text('Total: 85.15\\n')
if mode == 'sleep':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    pathlib.Path(sys.argv[2]).write_text(str(child.pid))
    def stop(signum, frame):
        child.wait()
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, stop)
    time.sleep(60)
print(json.dumps({'type': 'assistant', 'uuid': 'm1', 'message': {
    'content': [{'type': 'text', 'text': 'finished'}]}}), flush=True)
if mode == 'secret':
    print(os.environ['OPENAI_API_KEY'], file=sys.stderr)
if mode == 'ledger':
    text = subprocess.check_output([sys.executable, 'scripts/summarize.py',
                                   'task_folder/ledger.csv'], text=True)
    (root / 'output/result.txt').write_text(text)
if mode == 'inputs':
    instructions = (root / 'task_folder/TASK_INSTRUCTIONS.txt').read_text()
    source = (root / 'task_folder/source.txt').read_text()
    (root / 'output/result.txt').write_text(instructions + '\\n' + source)
if mode == 'truncated':
    print('{\\"type\\":\\"result\\"', flush=True)
else:
    if mode == 'invalidutf8':
        sys.stdout.buffer.write(b'{"type":"other","text":"' + bytes([255]) + b'"}\\n')
        sys.stdout.buffer.flush()
    print(json.dumps({'type':'result', 'subtype': 'success', 'is_error': False}), flush=True)
sys.exit(1 if mode == 'nonzero' else 0)
"""


class QwenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in (
            ("RUNS", self.root / "runs"),
            ("QWEN_MODEL", "test/model"),
            ("QWEN_BASE_URL", "http://127.0.0.1:12345/v1"),
            ("QWEN_API_KEY", "test-secret"),
        ):
            item = patch.object(config, name, value)
            item.start()
            self.addCleanup(item.stop)
        self.substrate = qwen.QwenSubstrate()
        self.adapter = JobBenchAdapter("qw", store_root=self.root / "store")
        self.source = self.root / "source"
        tree.write_tree(qwen.QWEN_SURFACE, {"systemprompt.md": "PROMPT_CANARY"}, self.source)

    def materialise(self, harness=None):
        installed = self.substrate.materialise(harness or self.source, self.adapter.spec, {})
        self.addCleanup(__import__("shutil").rmtree, installed.staging_dir, True)
        return installed

    def stream(self, events, suffix=""):
        path = self.root / "events.jsonl"
        path.write_text("diagnostic banner\n" + "\n".join(json.dumps(e) for e in events) + suffix)
        return path

    def test_materialisation_is_deterministic_and_rejects_unsupported_edits(self):
        harness = tree.read_tree(qwen.QWEN_SURFACE, self.source)
        harness.update(
            {
                "guardrails.md": "Use harness-probe for a compatibility probe.",
                "LongTermMEMORY.md": "MEMORY_CANARY",
                ".qwen/skills/harness-probe/SKILL.md": "---\nname: harness-probe\ndescription: run a compatibility probe\n---\nDo it.",
                "scripts/helper.py": "print('helper')",
                ".qwen/hooks.json": json.dumps(
                    {
                        "hooks": {
                            "PostToolUse": [
                                {
                                    "matcher": "write_file",
                                    "hooks": [{"type": "command", "command": "true"}],
                                }
                            ]
                        }
                    }
                ),
            }
        )
        tree.write_tree(qwen.QWEN_SURFACE, harness, self.source)
        before = runmark.fingerprint(self.source)
        left, right = self.materialise(), self.materialise()
        self.assertEqual(
            runmark.fingerprint(left.staging_dir), runmark.fingerprint(right.staging_dir)
        )
        self.assertEqual(before, runmark.fingerprint(self.source))
        self.assertEqual(before, left.fingerprint)
        memory = (left.staging_dir / "QWEN.md").read_text()
        self.assertIn("MEMORY_CANARY", memory)
        self.assertNotIn("PROMPT_CANARY", memory)
        self.assertEqual(
            (left.staging_dir / ".harness2/append-system-prompt.md").read_text(), "PROMPT_CANARY"
        )
        settings = json.loads((left.staging_dir / ".qwen/settings.json").read_text())
        self.assertEqual(settings["skills"]["enabled"], ["harness-probe"])
        self.assertEqual(settings["hooks"]["PostToolUse"][0]["hooks"][0]["timeout"], 10)
        for rel, value in (
            (".qwen/settings.json", "{}"),
            ("sub_agents/probe/AGENT.md", "---\ndescription: probe\n---\nprobe"),
            (
                ".qwen/hooks.json",
                '{"hooks":{"PostToolUse":[{"hooks":[{"type":"command",'
                '"command":"true","async":true}]}]}}',
            ),
            (
                ".qwen/skills/harness-probe/SKILL.md",
                "---\nname: harness-probe\ndescription: probe\nhooks: {}\n---\nprobe",
            ),
        ):
            edited = dict(harness)
            edited[rel] = value
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                qwen.validate_tree(edited)
        (self.source / "QWEN.md").write_text("uneditable")
        with self.assertRaises(ValueError):
            self.materialise()
        for pack in ("adapt", "tts"):
            self.assertIn(".qwen/skills", prompts._st(qwen.QWEN_SURFACE).surface_md)
            self.assertIn("delegation is unavailable", prompts._st("qwen").tts_surface)

    def test_protocol_pairs_parallel_calls_and_keeps_recovered_errors(self):
        first = message("assistant", [call("c1"), call("c2", name="read_file")])
        events = [
            first,
            first,
            message("user", [observation("c2", is_error=True)], "m2"),
            {"type": "future-event", "value": 1},
            message("user", [observation("c1")], "m3"),
            message("assistant", [{"type": "text", "text": "fixed"}], "m4"),
            SUCCESS,
        ]
        stream = self.stream(events)
        self.assertTrue(qwen.parse_events(stream).ok)
        text = self.substrate.read_trajectory(stream, obs_trunc=10_000, max_chars=100_000)
        self.assertEqual(text.count("[ACTION] write_file"), 1)
        self.assertIn("[OBSERVATION ERROR] [c2]", text)
        self.assertEqual(text.count("[ASSISTANT] fixed"), 1)
        self.assertIn("future-event", text)
        self.assertLessEqual(
            len(self.substrate.read_trajectory(stream, obs_trunc=20, max_chars=30)), 30
        )
        for broken in (
            events[:-1],
            [message("assistant", [call()]), SUCCESS],
            [message("user", [observation()]), SUCCESS],
            [{**SUCCESS, "subtype": "error_during_execution"}],
            [SUCCESS, message("assistant", [{"type": "text", "text": "late"}])],
            [first, {**first, "message": {"id": "m1", "content": []}}, SUCCESS],
            [{"type": "assistant", "message": None}, SUCCESS],
            [{"type": "system", "tools": ["agent"]}, SUCCESS],
            [{"type": "system", "tools": [{}]}, SUCCESS],
            [message("assistant", [{"type": "text", "text": {}}]), SUCCESS],
        ):
            with self.subTest(events=broken):
                path = self.stream(broken)
                self.assertFalse(qwen.parse_events(path).ok)
                self.assertIn(
                    "[HARNESS ERROR]",
                    self.substrate.read_trajectory(path, obs_trunc=10_000, max_chars=100_000),
                )
        self.assertFalse(qwen.parse_events(self.stream(events, '\n{"type": "result"')).ok)

    def test_environment_and_url_are_explicit(self):
        installed = self.materialise()
        with patch.dict(
            os.environ,
            {
                "QWEN_SYSTEM_MD": "bad",
                "ANTHROPIC_API_KEY": "private",
                "OPENAI_API_KEY": "personal",
                "HARNESS2_QWEN_API_KEY": "private-qwen",
                "GITHUB_TOKEN": "private-github",
                "CLI_VERSION": "fake",
                "NODE_OPTIONS": "--inspect",
                "QWEN_CODE_TRUSTED_FOLDERS_PATH": "/private/trust",
            },
        ):
            env = self.substrate.exec_env(qwen_home=self.root / "home", workspace=self.root)
        self.assertEqual(env["OPENAI_API_KEY"], "test-secret")
        for name in (
            "QWEN_SYSTEM_MD",
            "ANTHROPIC_API_KEY",
            "HARNESS2_QWEN_API_KEY",
            "GITHUB_TOKEN",
            "CLI_VERSION",
            "NODE_OPTIONS",
        ):
            self.assertNotIn(name, env)
        self.assertEqual(
            env["QWEN_CODE_TRUSTED_FOLDERS_PATH"],
            str((self.root / "home/trustedFolders.json").resolve()),
        )
        argv = self.substrate.exec_argv(install=installed, timeout=3)
        self.assertNotIn("test-secret", " ".join(argv))
        self.assertNotIn("--bare", argv)
        self.assertEqual(argv[0], str(config.QWEN_DIR / "node_modules/.bin/qwen"))
        for url in (
            "https://user:secret@host/v1",
            "https://host/v1?key=secret",
            "https://host/v1#secret",
            "file:///tmp/a",
            "https://host:bad/v1",
        ):
            with self.subTest(url=url), patch.object(config, "QWEN_BASE_URL", url):
                with self.assertRaises(config.ConfigError) as error:
                    qwen.endpoint()
                self.assertNotIn("secret", str(error.exception))

    def test_policy_is_not_overridden_and_unsupported_cells_fail_early(self):
        (self.root / ".env").write_text("QWEN_SYSTEM_MD=personal.md\n")
        with patch.object(Path, "home", return_value=self.root):
            with self.assertRaisesRegex(config.ConfigError, "personal environment"):
                qwen.home_env_identity()
        path = self.root / "system.json"
        path.write_text('{"permissions":{"deny":["write_file"]}}')
        with patch.dict(os.environ, {"QWEN_CODE_SYSTEM_SETTINGS_PATH": str(path)}):
            with self.assertRaisesRegex(config.ConfigError, "not overridden"):
                qwen.policy_identity()
        for bench in ("lab", "wb"):
            with self.assertRaisesRegex(ValueError, "JobBench"):
                _common.make_adapter(bench, None, "qw")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                setup.main([bench, "--substrate", "qw", "--dry-run"])
        with (
            patch.object(config, "SOLVER_MODEL_OC", ""),
            patch.object(config, "IMPROVER_MODEL", "local/improver"),
            patch.dict(os.environ, {"HARNESS2_QWEN_MODEL": "test/model"}),
        ):
            config.validate_models(substrate="qw")
        with patch.object(config, "QWEN_DIR", self.root / "absent"):
            with self.assertRaisesRegex(RuntimeError, "installation"):
                qwen.installed_identity()

    def fake_runtime(self, mode="ok", timeout=3):
        self.fake = self.root / "fake.py"
        self.fake.write_text(FAKE_CLI)
        dataset = self.root / "dataset"
        folder = dataset / "dataset/main/accounting/task1/task_folder"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "TASK_INSTRUCTIONS.txt").write_text("Create output/result.txt")
        for item in (
            patch.object(config, "jb_repo", return_value=dataset),
            patch(
                "harness2.substrate.qwen.installed_identity", return_value={"version": "fixture"}
            ),
            patch.object(
                self.adapter.substrate,
                "exec_argv",
                side_effect=lambda **kw: [
                    sys.executable,
                    str(self.fake),
                    mode,
                    str(self.root / "child.pid"),
                ],
            ),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.adapter.timeout = timeout

    def test_real_process_resume_artifact_identity_and_failure_diagnostics(self):
        self.fake_runtime()
        run_id = "jb-qw-agg1/accounting/task1/base1"
        out = self.adapter.execute("accounting/task1", self.source, run_id)
        traj = self.adapter._traj_dir("accounting/task1", run_id.replace("/", "__"))
        marker = (traj / ".adapt_complete").read_bytes()
        self.assertTrue(self.adapter.artifacts_intact(out))
        self.adapter.execute("accounting/task1", self.source, run_id)
        self.assertEqual(marker, (traj / ".adapt_complete").read_bytes())
        for changed in ("output", "capture", "model"):
            if changed == "output":
                (out / "result.txt").write_text("changed")
            elif changed == "capture":
                with (traj / "qwen-output.jsonl").open("a") as f:
                    f.write("\n")
            else:
                config.QWEN_MODEL = "different"
            self.assertFalse(self.adapter.artifacts_intact(out))
            self.adapter.execute("accounting/task1", self.source, run_id)
            self.assertTrue(self.adapter.artifacts_intact(out))
        archives = traj.parent / "_failed_attempts" / traj.name
        self.assertEqual(len(list(archives.iterdir())), 3)
        for mode in ("nonzero", "truncated", "invalidutf8", "secret"):
            with patch.object(
                self.adapter.substrate,
                "exec_argv",
                return_value=[sys.executable, str(self.fake), mode],
            ):
                rid = f"failure/{mode}"
                if mode == "secret":
                    self.adapter.execute("accounting/task1", self.source, rid)
                else:
                    with self.assertRaisesRegex(RuntimeError, "incomplete"):
                        self.adapter.execute("accounting/task1", self.source, rid)
                td = self.adapter._traj_dir("accounting/task1", rid.replace("/", "__"))
                self.assertEqual((td / ".adapt_complete").exists(), mode == "secret")
                if mode != "secret":
                    self.assertIsNone(
                        self.adapter.score(
                            self.adapter._out_dir("accounting/task1", rid.replace("/", "__")),
                            "accounting/task1",
                        )
                    )
                for path in td.iterdir():
                    self.assertNotIn("test-secret", path.read_text())

    def test_changed_task_inputs_rerun_with_current_source_material(self):
        self.fake_runtime(mode="inputs")
        task_id = "accounting/task1"
        run_id = "inputs/base1"
        folder = self.adapter.task_files(task_id)
        instructions = folder / "TASK_INSTRUCTIONS.txt"
        source = folder / "source.txt"
        source.write_text("version one")
        with patch.object(
            self.adapter.substrate, "execute", wraps=self.adapter.substrate.execute
        ) as execute:
            for count, changed in enumerate((None, source, instructions), start=1):
                with self.subTest(changed=changed):
                    if changed is not None:
                        changed.write_text("version two")
                    out = self.adapter.execute(task_id, self.source, run_id)
                    self.assertEqual(execute.call_count, count)
                    self.assertEqual(
                        (out / "result.txt").read_text(),
                        instructions.read_text() + "\n" + source.read_text(),
                    )
                    self.adapter.execute(task_id, self.source, run_id)
                    self.assertEqual(execute.call_count, count)

    def test_timeout_and_cancellation_reap_descendants(self):
        for mode in ("timeout", "cancel"):
            with self.subTest(mode=mode):
                self.fake_runtime(mode="sleep", timeout=1 if mode == "timeout" else 20)
                if mode == "timeout":
                    with self.assertRaisesRegex(RuntimeError, "timeout"):
                        self.adapter.execute("accounting/task1", self.source, "timeout")
                else:
                    (self.root / "child.pid").unlink()
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(
                            self.adapter.execute, "accounting/task1", self.source, "cancel"
                        )
                        deadline = time.monotonic() + 10
                        while (
                            not (self.root / "child.pid").exists() and time.monotonic() < deadline
                        ):
                            time.sleep(0.02)
                        self.adapter.qwen_runner.cancel()
                        with self.assertRaisesRegex(RuntimeError, "cancelled"):
                            future.result(timeout=10)
                pid = int((self.root / "child.pid").read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
                self.assertFalse(self.adapter.qwen_runner._active)
                td = self.adapter._traj_dir("accounting/task1", mode)
                self.assertFalse((td / ".adapt_complete").exists())
                self.adapter.qwen_runner = runner.QwenRunner(self.adapter.substrate)

    def test_recursive_jobbench_fixture_preserves_budget_library_and_resume(self):
        from dataclasses import replace

        from harness2.core import judge
        from harness2.core.spec import ChannelSpec

        self.fake_runtime(mode="ledger")
        base = tree.read_tree(qwen.QWEN_SURFACE, self.source)
        base["scripts/summarize.py"] = BASE_SCRIPT
        tree.write_tree(qwen.QWEN_SURFACE, base, self.source)
        self.adapter.base_harness = lambda: self.source
        folder = config.jb_repo() / "dataset/main/accounting/task1/task_folder"
        (folder / "ledger.csv").write_text("amount\n100.25\n-20.10\n5.00\n")
        second = folder.parent.parent / "task2/task_folder"
        second.mkdir(parents=True)
        (second / "TASK_INSTRUCTIONS.txt").write_text("Sum every signed amount in ledger.csv")
        (second / "ledger.csv").write_text("amount\n100.25\n-20.10\n5.00\n")
        channel = ChannelSpec("opencode", "local/deterministic")
        self.adapter.spec = replace(
            self.adapter.spec, channels={"proposer": channel, "selector": channel}
        )
        proposer = DemoProposer(channel)
        for k in (1, 2):
            with self.subTest(k=k):
                kwargs = dict(
                    adapter=self.adapter,
                    prop=proposer,
                    runs_root=self.root / "workflow",
                    library_root=self.root / f"library{k}",
                )
                first = schedule.run_domain("accounting", self.adapter.tasks(), k, **kwargs)
                self.assertEqual((first["done"], first["failed"]), (2, 0), first["tasks"])
                for tid in self.adapter.tasks():
                    path = self.root / "workflow" / f"jb-qw-agg{k}" / tid.replace("/", "__")
                    summary = json.loads((path / "summary.json").read_text())
                    for mode in ("agg", "seq"):
                        ledger = summary["modes"][mode]["ledger"]
                        self.assertEqual((ledger["R"], ledger["P"]), (4 * k, 3 * k))
                        selection = summary["selections"][mode]
                        chosen = Path(selection["chosen_run_dir"])
                        self.assertEqual((chosen / "result.txt").read_text(), "Total: 85.15\n")
                        self.assertTrue(self.adapter.artifacts_intact(chosen))
                self.assertEqual(first["tasks"][1]["library_entries_before"], {"agg": 1, "seq": 1})
                before = len(proposer._calls)
                with patch.object(
                    self.adapter.substrate, "execute", side_effect=AssertionError("rerun")
                ):
                    resumed = schedule.run_domain("accounting", self.adapter.tasks(), k, **kwargs)
                self.assertEqual(
                    [row["status"] for row in resumed["tasks"]], ["skipped", "skipped"]
                )
                self.assertEqual(before, len(proposer._calls))
                ns = self.root / "workflow" / f"jb-qw-agg{k}"
                from harness2.cli.judge import collect_selections

                self.adapter.score = lambda *args: {
                    "score": 1.0,
                    "max_score": 1.0,
                    "n_passed": 1,
                    "n_criteria": 1,
                }
                scored = judge.judge_selections(
                    self.adapter, collect_selections(ns, ("agg", "seq")), parallel=1
                )
                self.assertTrue(all(row.graded for row in scored))

    def test_dry_run_and_namespace_identity(self):
        self.fake_runtime()
        args = run_sweep._build_parser().parse_args(
            [
                "--bench",
                "jb",
                "--substrate",
                "qw",
                "--max-tasks",
                "1",
                "--dry-run",
            ]
        )
        self.assertFalse(config.RUNS.exists())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_sweep._main(args), 0)
        self.assertIn("jb-qw-agg1", output.getvalue())
        self.assertFalse(config.RUNS.exists())
        ns = self.root / "namespace"
        run_sweep._check_identity(self.adapter, args, ns)
        identity = json.loads((ns / "config.json").read_text())
        self.assertEqual(identity["executor"], "test/model")
        self.assertNotIn("test-secret", (ns / "config.json").read_text())
        config.QWEN_API_KEY = "rotated"
        run_sweep._check_identity(self.adapter, args, ns)
        config.QWEN_MODEL = "other"
        with self.assertRaisesRegex(ValueError, "different models"):
            run_sweep._check_identity(self.adapter, args, ns)

    def test_signal_scope_cleans_running_worker_before_pool_wait(self):
        self.fake_runtime(mode="sleep", timeout=20)
        before = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(KeyboardInterrupt):
            with self.adapter.qwen_runner.scope(), ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    self.adapter.execute, "accounting/task1", self.source, "signal"
                )
                deadline = time.monotonic() + 10
                while not (self.root / "child.pid").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                os.kill(os.getpid(), signal.SIGTERM)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)
        self.assertFalse(self.adapter.qwen_runner._active)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            future.result()


class LocalModel:
    """Deterministic model replies; the CLI still performs real file/skill/hook execution."""

    def __init__(self, responder):
        self.requests = []
        self.errors = []
        self.responder = responder

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(body)
                try:
                    content = owner.responder(body)
                except Exception as exc:
                    owner.errors.append(str(exc))
                    content = {"role": "assistant", "content": "Mock responder failed"}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                finish = "tool_calls" if content.get("tool_calls") else "stop"
                for delta, reason in ((content, None), ({}, finish)):
                    event = {
                        "id": "completion",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "fixture",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
                    }
                    self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.patch = patch.object(
            config, "QWEN_BASE_URL", f"http://127.0.0.1:{self.server.server_port}/v1"
        )
        self.patch.start()
        return self

    def __exit__(self, *args):
        self.patch.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @staticmethod
    def tool(name, arguments, iid):
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": iid,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        }


@unittest.skipUnless(
    os.environ.get("HARNESS2_TEST_QWEN_CLI") == "1",
    "set HARNESS2_TEST_QWEN_CLI=1 after installing the pinned Qwen package",
)
class RealQwenTests(unittest.TestCase):
    setUp = QwenTests.setUp
    materialise = QwenTests.materialise

    def test_published_cli_with_native_skills_hooks_and_isolated_home(self):
        qwen.installed_identity()
        harness = {
            "systemprompt.md": "APPEND_CANARY",
            "guardrails.md": "Use harness-probe when probing.",
            "LongTermMEMORY.md": "MEMORY_CANARY",
            ".qwen/skills/harness-probe/SKILL.md": (
                "---\nname: harness-probe\ndescription: SKILL_CANARY compatibility probe\n"
                "---\nSKILL_BODY_CANARY\nRun python3 $SKILL_DIR/scripts/helper.py.\n"
            ),
            ".qwen/skills/harness-probe/scripts/helper.py": "from pathlib import Path\nPath('output/result.txt').write_text('CLI_HELPER_RESULT')\n",
            "scripts/hook.py": (
                "import json, sys\nx=json.load(sys.stdin)\ne=x['hook_event_name']\n"
                "if e=='PreToolUse' and x['tool_name']=='write_file':\n"
                " print(json.dumps({'hookSpecificOutput':{'hookEventName':e,"
                "'permissionDecision':'deny','permissionDecisionReason':'DENY_CANARY'}}))\n"
                "elif e=='PostToolUse':\n"
                " print(json.dumps({'hookSpecificOutput':{'hookEventName':e,"
                "'additionalContext':'POSTHOOK_CANARY'}}))\n"
            ),
            ".qwen/hooks.json": json.dumps(
                {
                    "hooks": {
                        event: [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": f"{shlex.quote(sys.executable)} scripts/hook.py",
                                        "timeout": 3,
                                    }
                                ],
                            }
                        ]
                        for event in ("PreToolUse", "PostToolUse")
                    }
                }
            ),
        }
        tree.write_tree(qwen.QWEN_SURFACE, harness, self.source)
        installed = self.materialise()

        def respond(body):
            outputs = [m for m in body["messages"] if m["role"] == "tool"]
            if not outputs:
                return LocalModel.tool("skill", {"skill": "harness-probe"}, "load_skill")
            if len(outputs) == 1:
                task = "\n".join(
                    qwen._block_text(m["content"]) for m in body["messages"] if m["role"] == "user"
                )
                output_dir = task.split("=== OUTPUT DIRECTORY ===\n", 1)[1].splitlines()[0]
                return LocalModel.tool(
                    "write_file",
                    {
                        "file_path": str(Path(output_dir) / "blocked.txt"),
                        "content": "should be denied",
                    },
                    "denied_write",
                )
            if len(outputs) == 2:
                return LocalModel.tool(
                    "run_shell_command",
                    {
                        "command": "python3 .qwen/skills/harness-probe/scripts/helper.py",
                        "description": "Run installed skill helper",
                    },
                    "helper",
                )
            return {"role": "assistant", "content": "complete"}

        personal = self.root / "personal"
        personal.mkdir()
        (personal / "QWEN.md").write_text("PERSONAL_CONTEXT_CANARY")
        native_env = self.adapter.substrate.exec_env

        def poisoned_ancestor(**kwargs):
            workspace = kwargs["workspace"]
            (workspace.parent / "QWEN.md").write_text("ANCESTOR_CONTEXT_CANARY")
            override = workspace.parent / "override.md"
            override.write_text("ENV_OVERRIDE_CANARY")
            (workspace.parent / ".env").write_text(f"QWEN_SYSTEM_MD={override}\n")
            return native_env(**kwargs)

        with (
            LocalModel(respond) as model,
            patch.object(self.adapter.substrate, "exec_env", side_effect=poisoned_ancestor),
            patch.dict(os.environ, {"QWEN_HOME": str(personal)}),
        ):
            for index in range(2):
                folder = self.root / "inputs"
                folder.mkdir(exist_ok=True)
                (folder / "TASK_INSTRUCTIONS.txt").write_text("Complete the probe")
                self.adapter.qwen_runner.run(
                    task_folder=folder,
                    install=installed,
                    out=self.root / f"out{index}",
                    trajectory=self.root / f"traj{index}",
                    timeout=20,
                    identity=self.substrate.execution_identity(),
                )
        all_requests = json.dumps(model.requests)
        self.assertFalse(model.errors, model.errors)
        self.assertFalse("ANCESTOR_CONTEXT_CANARY" in all_requests)
        self.assertFalse("PERSONAL_CONTEXT_CANARY" in all_requests)
        self.assertFalse("ENV_OVERRIDE_CANARY" in all_requests)
        for canary in (
            "APPEND_CANARY",
            "MEMORY_CANARY",
            "SKILL_CANARY",
            "SKILL_BODY_CANARY",
            "DENY_CANARY",
            "POSTHOOK_CANARY",
        ):
            self.assertTrue(canary in all_requests, f"CLI did not observe {canary}")
        for index in range(2):
            self.assertEqual(
                (self.root / f"out{index}/result.txt").read_text(), "CLI_HELPER_RESULT"
            )
            self.assertFalse((self.root / f"out{index}/blocked.txt").exists())
            transcript = qwen.parse_events(self.root / f"traj{index}/qwen-output.jsonl")
            self.assertTrue(transcript.ok, transcript.errors)

    def test_published_cli_jobbench_resume(self):
        dataset = self.root / "dataset"
        folder = dataset / "dataset/main/accounting/task1/task_folder"
        folder.mkdir(parents=True)
        (folder / "TASK_INSTRUCTIONS.txt").write_text("Create output/result.txt")

        def respond(body):
            if not any(m["role"] == "tool" for m in body["messages"]):
                task = "\n".join(
                    qwen._block_text(m["content"]) for m in body["messages"] if m["role"] == "user"
                )
                output = task.split("=== OUTPUT DIRECTORY ===\n", 1)[1].splitlines()[0]
                return LocalModel.tool(
                    "write_file",
                    {
                        "file_path": str(Path(output) / "result.txt"),
                        "content": "ADAPTER_RESULT",
                    },
                    "write",
                )
            return {"role": "assistant", "content": "complete"}

        with patch.object(config, "jb_repo", return_value=dataset), LocalModel(respond) as model:
            out = self.adapter.execute("accounting/task1", self.source, "real/base1")
            self.assertEqual((out / "result.txt").read_text(), "ADAPTER_RESULT")
            self.assertTrue(self.adapter.artifacts_intact(out))
            calls = len(model.requests)
            self.adapter.execute("accounting/task1", self.source, "real/base1")
            self.assertEqual(calls, len(model.requests))
            (out / "result.txt").write_text("tampered")
            self.assertFalse(self.adapter.artifacts_intact(out))
            with patch.object(self.adapter, "_run_judge") as judge:
                self.assertIsNone(self.adapter.score(out, "accounting/task1"))
                judge.assert_not_called()
            self.adapter.execute("accounting/task1", self.source, "real/base1")
            self.assertGreater(len(model.requests), calls)
            self.assertEqual((out / "result.txt").read_text(), "ADAPTER_RESULT")
            self.assertTrue(self.adapter.artifacts_intact(out))
        self.assertFalse(model.errors, model.errors)
