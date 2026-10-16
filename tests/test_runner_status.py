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

"""Replay the end-to-end Bash runners without running benchmark services."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
RUNNERS = ("run_lab.sh", "run_jobbench.sh", "run_workbuddy.sh")
STAGES = ("harness2-check", "harness2", "harness2-judge", "harness2-report")


@unittest.skipUnless(BASH, "Bash is required to run benchmark shell scripts")
class RunnerStatusTests(unittest.TestCase):
    def run_runner(self, runner, statuses, *args):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy2(ROOT / "scripts" / runner, scripts / runner)
            binaries = root / "bin"
            binaries.mkdir()
            trace = root / "trace.jsonl"
            # Replace only the benchmark command boundaries. The actual runner
            # still resolves PATH, runs Bash arrays, and computes its exit status.
            source = (
                f"#!{sys.executable}\n"
                + """import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
with open(os.environ['RUNNER_TRACE'], 'a', encoding='utf-8') as trace:
    trace.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
raise SystemExit(json.loads(os.environ['RUNNER_STATUSES']).get(name, 0))
"""
            )
            for stage in STAGES:
                command = binaries / stage
                command.write_text(source, encoding="utf-8")
                command.chmod(0o755)
            env = {
                key: os.environ[key]
                for key in ("HOME", "TMPDIR", "LANG", "LC_ALL", "TERM")
                if key in os.environ
            }
            env.update(
                PATH=str(binaries) + os.pathsep + os.defpath,
                RUNNER_TRACE=str(trace),
                RUNNER_STATUSES=json.dumps(statuses),
            )
            result = subprocess.run(
                [BASH, str(scripts / runner), *args],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            calls = [json.loads(line) for line in trace.read_text().splitlines()]
            return result, calls

    def test_report_failure_makes_workflow_fail(self):
        for runner in RUNNERS:
            with self.subTest(runner=runner):
                result, calls = self.run_runner(runner, {"harness2-report": 7})
                self.assertEqual([call[0] for call in calls], list(STAGES))
                self.assertEqual(result.returncode, 7, result.stderr)

    def test_success_and_existing_error_precedence(self):
        for runner in RUNNERS:
            for statuses, expected in (
                ({}, 0),
                ({"harness2": 2, "harness2-judge": 3, "harness2-report": 7}, 2),
                ({"harness2-judge": 3, "harness2-report": 7}, 3),
            ):
                with self.subTest(runner=runner, statuses=statuses):
                    result, calls = self.run_runner(runner, statuses)
                    self.assertEqual([call[0] for call in calls], list(STAGES))
                    self.assertEqual(result.returncode, expected, result.stderr)

    def test_check_failure_stops_before_adaptation(self):
        for runner in RUNNERS:
            with self.subTest(runner=runner):
                result, calls = self.run_runner(runner, {"harness2-check": 5})
                self.assertEqual(result.returncode, 5, result.stderr)
                self.assertEqual([call[0] for call in calls], ["harness2-check"])

    def test_dry_run_stops_before_judging_and_reporting(self):
        for runner in RUNNERS:
            with self.subTest(runner=runner):
                result, calls = self.run_runner(runner, {"harness2": 2}, "--dry-run")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual([call[0] for call in calls], list(STAGES[:2]))
                self.assertIn("--dry-run", calls[1])


if __name__ == "__main__":
    unittest.main()
