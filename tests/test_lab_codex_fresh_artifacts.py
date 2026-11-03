# SPDX-License-Identifier: Apache-2.0
"""Run the bundled entry point twice with an offline Codex process fixture."""

import importlib.util
import json
import runpy
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "third_party/lab_runner/adapt_runs_cx/run_codex_lab.py"


class LabCodexFreshArtifactsTests(unittest.TestCase):
    def test_repeat_run_never_serves_prior_outputs_or_completion_marker(self):
        for empty_stream in (False, True):
            with (
                self.subTest(empty_stream=empty_stream),
                tempfile.TemporaryDirectory(dir=ROOT) as td,
            ):
                root = Path(td)
                spec = importlib.util.spec_from_file_location(
                    "fixture_config", ROOT / "third_party/lab_runner/tts_lab/config.py"
                )
                config = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(config)
                config.TASKS_DIR = root / "tasks"
                task = config.TASKS_DIR / "accounting/task1"
                (task / "documents").mkdir(parents=True)
                (task / "task.json").write_text(
                    json.dumps({"instructions": "Write the requested ledger."})
                )
                external = types.ModuleType("external")
                tts = types.ModuleType("external.tts_lab")
                tts.config = config
                with patch.dict(sys.modules, {"external": external, "external.tts_lab": tts}):
                    module = runpy.run_path(str(RUNNER))
                main = module["main"]
                main.__globals__.update(
                    SCRATCH_BASE=root / "scratch",
                    RESULTS_ROOT=root / "results",
                    CODEX_BIN=Path(sys.executable),
                    READ_DOC=ROOT / "third_party/lab_runner/opencode/scripts/read_doc.py",
                )
                args = module["parser"].parse_args(
                    ["--task", "accounting/task1", "--run-id", "fixture", "--model", "fixture"]
                )
                calls = 0

                def executor(argv, *, cwd, env, stdin, stdout, stderr):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        (Path(cwd) / "output/ledger.txt").write_text("First attempt's ledger")
                    if calls == 1 or not empty_stream:
                        stdout.write(
                            json.dumps(
                                {
                                    "type": "turn.completed",
                                    "usage": {"input_tokens": 1, "output_tokens": 1},
                                }
                            )
                            + "\n"
                        )
                    return subprocess.CompletedProcess(argv, 0)

                old_handlers = {
                    s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
                }
                try:
                    with patch.object(main.__globals__["subprocess"], "run", side_effect=executor):
                        self.assertEqual(main(args), 0)
                        self.assertEqual(main(args), 2 if empty_stream else 1)
                finally:
                    for signum, handler in old_handlers.items():
                        signal.signal(signum, handler)
                run = root / "results/fixture"
                self.assertFalse((run / "output/ledger.txt").exists())
                if empty_stream:
                    self.assertFalse((run / "metrics.json").exists())
                else:
                    metrics = json.loads((run / "metrics.json").read_text())
                    self.assertEqual(metrics["deliverables"], [])
                    self.assertFalse(metrics["finished_cleanly"])
