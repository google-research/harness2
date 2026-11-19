# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
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

"""Actual Bash runners and local score-cache protocol fixtures; no model runs."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness2.core.schedule import Namespace, TaskResult, _write_summary
from harness2.core.selection import Selection

ROOT = Path(__file__).resolve().parents[1]


def write_fixture(root, seed, passed):
    run = root / ("run-" + str(seed))
    run.mkdir()
    (run / "scores.json").write_text(
        json.dumps({"n_passed": passed, "n_criteria": 2, "all_pass": passed == 2})
    )
    ns = Namespace("lab", "oc", "agg", 1, seed=seed)
    selection = Selection(
        task_id="contracts/task-1",
        mode="agg",
        k=1,
        chosen_label="agg1",
        chosen_run_id="fixture-" + str(seed),
        ok=True,
    ).to_dict()
    selection["chosen_run_dir"] = str(run)
    result = TaskResult(
        task_id="contracts/task-1",
        domain="contracts",
        k=1,
        status="ok",
        modes={"agg": {"ok": True}},
        selections={"agg": selection},
    )
    task_dir = ns.task_dir(root / "lab/adapt", "contracts/task-1")
    task_dir.mkdir(parents=True, exist_ok=True)
    _write_summary(task_dir / "summary.json", result)


class RunnerSeedTests(unittest.TestCase):
    def run_script(self, runner, args, *, real=False, stale=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            shutil.copyfile(ROOT / "scripts" / runner, root / "scripts" / runner)
            (root / "bin").mkdir()
            trace = root / "trace.jsonl"
            if stale:
                write_fixture(root, None, 0)
            source = (
                f"#!{sys.executable}\n"
                + """import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
with open(os.environ['TRACE'], 'a') as f:
    f.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
if os.environ['REAL'] == '1':
    if name == 'harness2':
        import argparse
        from test_runner_seed import write_fixture
        p = argparse.ArgumentParser()
        p.add_argument('--seed', type=int)
        a, _ = p.parse_known_args()
        write_fixture(Path(os.environ['HARNESS2_RUNS_DIR']), a.seed, 1)
    elif name == 'harness2-judge':
        from harness2.cli.judge import main
        raise SystemExit(main())
    elif name == 'harness2-report':
        from harness2.cli.report import main
        raise SystemExit(main())
"""
            )
            for stage in ("harness2-check", "harness2", "harness2-judge", "harness2-report"):
                command = root / "bin" / stage
                command.write_text(source)
                command.chmod(0o755)
            env = {k: os.environ[k] for k in ("HOME", "TMPDIR", "LANG") if k in os.environ}
            env.update(
                PATH=str(root / "bin") + os.pathsep + os.defpath,
                TRACE=str(trace),
                REAL=str(int(real)),
                PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / "tests"),
                HARNESS2_RUNS_DIR=str(root),
                HARNESS2_SOLVER_MODEL="fixture/executor",
                HARNESS2_JUDGE_MODEL="fixture-judge",
                PYTHONDONTWRITEBYTECODE="1",
            )
            result = subprocess.run(
                ["/bin/bash", str(root / "scripts" / runner), *args],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            calls = [json.loads(line) for line in trace.read_text().splitlines()]
            judged = root / "lab/adapt/lab-oc-agg1-s7/judged.json"
            return result, calls, judged.exists()

    def test_seed_reaches_every_stage_for_each_runner(self):
        for runner in ("run_lab.sh", "run_jobbench.sh", "run_workbuddy.sh"):
            for args, seed in (
                (["--seed", "7"], "7"),
                (["--seed=-3"], "-3"),
                (["--seed", "2", "--seed=7"], "7"),
            ):
                with self.subTest(runner=runner, args=args):
                    result, calls, _ = self.run_script(runner, args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    for call in calls[2:]:
                        self.assertIn("--seed", call)
                        self.assertEqual(call[call.index("--seed") + 1], seed)

    def test_seedless_workflow_keeps_its_existing_arguments(self):
        result, calls, _ = self.run_script("run_lab.sh", [])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("--seed" in call for call in calls))

    def test_actual_judge_and_report_consume_the_seeded_summary(self):
        for stale in (False, True):
            with self.subTest(stale_unseeded_summary=stale):
                result, _, judged = self.run_script(
                    "run_lab.sh", ["--seed", "7"], real=True, stale=stale
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(judged)
                self.assertIn("lab-oc seed=7", result.stdout)
                self.assertIn("50.00%", result.stdout)
