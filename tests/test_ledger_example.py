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

"""The getting-started ledger example: run, inspect, resume, change inputs (#55)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/getting_started/ledger_run.py"
INPUTS = ROOT / "examples/getting_started/inputs"


class LedgerExampleTests(unittest.TestCase):
    def run_example(self, inputs: Path, output: Path, *extra: str):
        env = {k: os.environ[k] for k in ("HOME", "PATH", "TMPDIR", "LANG") if k in os.environ}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(EXAMPLE), "--inputs", str(inputs), "--output", str(output), *extra],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
        )

    def test_sample_inputs_pass_and_inspect_reads_saved_answers(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "run"
            first = self.run_example(INPUTS, out)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertIn("Total: 85.15", first.stdout)
            self.assertIn("Total: 20.25", first.stdout)
            again = self.run_example(INPUTS, out)  # resume: finished tasks skipped
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertIn("skipped", again.stdout)

    def test_changed_inputs_rerun_only_the_changed_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = Path(temporary) / "inputs"
            inputs.mkdir()
            # arbitrary names: a task is whatever your CSVs are called
            (inputs / "week-1.csv").write_text("amount\n10.00\n-3.00\n")
            (inputs / "week-2.csv").write_text("amount\n5.00\n")
            out = Path(temporary) / "run"
            first = self.run_example(inputs, out)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertIn("week-1", first.stdout)
            self.assertIn("Total: 7.00", first.stdout)
            (inputs / "week-1.csv").write_text("amount\n10.00\n-3.00\n-4.00\n")
            changed = self.run_example(inputs, out)
            self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
            self.assertIn("Total: 3.00", changed.stdout)  # the new sum, not the stale 7.00
            self.assertIn("re-running", changed.stdout)

    def test_inspection_fails_for_an_incorrect_saved_answer(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "run"
            self.assertEqual(self.run_example(INPUTS, out).returncode, 0)
            summary = json.loads(
                (out / "runs/demo-oc-agg1/ledger-1/summary.json").read_text()
            )
            answer = Path(summary["selections"]["agg"]["chosen_run_dir"]) / "answer.txt"
            answer.write_text("Total: 99.99\n")
            broken = self.run_example(INPUTS, out, "--inspect-only")
            self.assertEqual(broken.returncode, 1, broken.stdout + broken.stderr)
            self.assertIn("FAIL", broken.stdout)


if __name__ == "__main__":
    unittest.main()
