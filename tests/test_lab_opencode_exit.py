# SPDX-License-Identifier: Apache-2.0
"""Exercise the bundled CLI with offline task/render and process fixtures."""

import importlib.util
import json
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "third_party/lab_runner/opencode/run_opencode.py"


def module(name, **attrs):
    result = types.ModuleType(name)
    result.__dict__.update(attrs)
    return result


def run_fixture(returncode, root):
    docs = root / "documents"
    docs.mkdir()
    checkout = root / "checkout"
    (checkout / "packages/opencode").mkdir(parents=True)
    spec = importlib.util.spec_from_file_location(
        "fixture_session", ROOT / "third_party/lab_runner/opencode/session.py"
    )
    session = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(session)
    rendered = types.SimpleNamespace(
        prompt="Fixture task",
        agent=None,
        mode="fixture",
        base_skills=[],
        harness_skills=[],
        subagents=[],
        scripts=[],
        plugins=[],
        plugin_tools=[],
        opencode_only=False,
    )
    modules = {
        "harness": module("harness"),
        "harness.run": module(
            "harness.run",
            BENCH_ROOT=root,
            DEFAULT_SKILLS=[],
            _load_env=lambda: None,
            load_task=lambda **kwargs: {
                "docs_dir": docs,
                "instructions": "Write the requested ledger.",
            },
        ),
        "harness.tools": module("harness.tools", get_all_tool_definitions=lambda: []),
        "opencode": module("opencode", session=session),
        "opencode.render": module(
            "opencode.render",
            RenderResult=types.SimpleNamespace,
            render_tree=lambda *args, **kwargs: rendered,
        ),
        "utils": module("utils"),
        "utils.stdio": module("utils.stdio", force_utf8_stdio=lambda: None),
    }

    def executor(argv, **kwargs):
        kwargs["stdout"].write(
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {
                        "type": "step-finish",
                        "tokens": {"input": 1, "output": 1},
                        "cost": 0,
                    },
                }
            )
            + "\n"
        )
        if returncode == 0:
            (root / "results/fixture/workspace/output/ledger.txt").write_text(
                "The requested ledger."
            )
        return subprocess.CompletedProcess(argv, returncode)

    argv = [
        str(RUNNER),
        "--task",
        "accounting/task1",
        "--run-id",
        "fixture",
        "--model",
        "fixture",
        "--results-root",
        str(root / "results"),
        "--opencode-dir",
        str(checkout),
        "--max-continue",
        "0",
    ]
    with (
        patch.dict(sys.modules, modules),
        patch.object(sys, "argv", argv),
        patch.object(subprocess, "run", side_effect=executor),
    ):
        runpy.run_path(str(RUNNER), run_name="__main__")


class LabOpencodeExitTests(unittest.TestCase):
    def test_cli_propagates_executor_failure_and_preserves_success(self):
        for returncode in (0, 42):
            with self.subTest(returncode=returncode), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), str(returncode), str(root)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, returncode, result.stdout + result.stderr)
                metrics = json.loads((root / "results/fixture/metrics.json").read_text())
                self.assertEqual(metrics["exit_code"], returncode)
                self.assertEqual(metrics["finished_cleanly"], returncode == 0)


if __name__ == "__main__":
    run_fixture(int(sys.argv[1]), Path(sys.argv[2]))
