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

"""Regressions for re-asserting persisted R=4k / P=3k ledger identity on resume, judge, and report."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock

from harness2.cli import judge as judge_cli
from harness2.cli import report as report_cli
from harness2.core import judge, ledger, report, schedule
from harness2.core.selection import Selection
from harness2.demo import DemoAdapter, DemoProposer


class PersistedBudgetIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.proposer = DemoProposer(self.adapter.spec.channels["proposer"])

    def _valid_ledger_dict(self, mode: str = "agg", k: int = 1) -> dict:
        led = ledger.Ledger(mode=mode, task_id="ledger-1", k=k)
        for i in range(1, k + 1):
            led.rollout(f"base{i}")
        for i in range(1, k + 1):
            led.session(f"inv{i}")
            led.session(f"dom{i}")
            led.rollout(f"inv{i}")
            led.rollout(f"dom{i}")
            final = f"agg{i}" if mode == "agg" else f"c{i}"
            led.session(final)
            led.rollout(final)
        led.assert_identity()
        return led.to_dict()

    def test_persisted_violation_accepts_valid_and_legacy_ledgers(self):
        for k in (1, 2):
            for mode in ("agg", "seq"):
                with self.subTest(mode=mode, k=k):
                    self.assertEqual(
                        ledger.persisted_violation(self._valid_ledger_dict(mode, k), k=k), ""
                    )
        self.assertEqual(ledger.persisted_violation(None, k=1), "")
        self.assertEqual(
            schedule.budget_violations({"k": 1, "modes": {"agg": {"ok": True}}}, ("agg",)),
            {},
        )

    def test_persisted_violation_catches_truncated_counts_duplicates_and_legacy_arms(self):
        short_r = self._valid_ledger_dict("agg", 1)
        short_r["R"] = 2
        short_r["rollouts"] = short_r["rollouts"][:2]
        self.assertIn("R=2/4, P=3/3", ledger.persisted_violation(short_r, k=1))

        short_p = self._valid_ledger_dict("seq", 2)
        short_p["P"] = 5
        short_p["sessions"] = short_p["sessions"][:5]
        self.assertIn("R=8/8, P=5/6", ledger.persisted_violation(short_p, k=2))

        dup = self._valid_ledger_dict("agg", 1)
        dup["rollouts"] = ["base1", "inv1", "inv1", "agg1"]
        self.assertIn("duplicate labels ['inv1']", ledger.persisted_violation(dup, k=1))

        mismatch = self._valid_ledger_dict("agg", 1)
        mismatch["rollouts"] = ["base1", "inv1"]
        self.assertIn("rollouts list (2) != R=4", ledger.persisted_violation(mismatch, k=1))

        self.assertIn("invalid ledger record", ledger.persisted_violation("bad-ledger", k=1))

        legacy_arms = {"k": 1, "arms": {"agg": {"ok": True, "ledger": short_r}}}
        self.assertEqual(
            schedule.budget_violations(legacy_arms, ("agg", "seq")),
            {"agg": "R=2/4, P=3/3"},
        )

    def test_resume_reruns_task_when_persisted_ledger_violates_budget(self):
        first = schedule.run_domain(
            "accounting",
            self.adapter.tasks(),
            1,
            adapter=self.adapter,
            prop=self.proposer,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "library",
        )
        self.assertEqual([t["status"] for t in first["tasks"]], ["ok", "ok"])

        summary_path = self.adapter.spec.runs_root / "demo-oc-agg1/ledger-1/summary.json"
        summary = json.loads(summary_path.read_text())
        summary["modes"]["agg"]["ledger"]["R"] = 2
        summary["modes"]["agg"]["ledger"]["rollouts"] = ["base1", "agg1"]
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        executions_before = self.adapter.execution_count
        repaired = schedule.run_domain(
            "accounting",
            self.adapter.tasks(),
            1,
            adapter=self.adapter,
            prop=self.proposer,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "library",
        )
        self.assertEqual([t["status"] for t in repaired["tasks"]], ["ok", "skipped"])
        self.assertGreater(self.adapter.execution_count, executions_before)
        restored = json.loads(summary_path.read_text())
        self.assertEqual(restored["modes"]["agg"]["ledger"]["R"], 4)

    def test_judge_declines_budget_violating_mode_and_report_warns(self):
        run_dir = self.root / "lab-run"
        run_dir.mkdir()
        (run_dir / "scores.json").write_text(
            json.dumps({"n_passed": 2, "n_criteria": 2, "all_pass": True}), encoding="utf-8"
        )
        namespace = schedule.Namespace("lab", "oc", "agg", 1)
        ns_dir = self.root / "lab/adapt" / str(namespace)
        task_dir = namespace.task_dir(self.root / "lab/adapt", "contracts/task-1")
        task_dir.mkdir(parents=True)

        bad_ledger = self._valid_ledger_dict("agg", 1)
        bad_ledger["R"] = 2
        bad_ledger["rollouts"] = ["base1", "agg1"]

        selected = Selection(
            task_id="contracts/task-1",
            mode="agg",
            k=1,
            chosen_label="agg1",
            chosen_run_id="adapted-1",
            ok=True,
        ).to_dict()
        selected["chosen_run_dir"] = str(run_dir)
        selected["selected_at"] = "2026-11-24T00:00:00+00:00"

        res = schedule.TaskResult(
            task_id="contracts/task-1",
            domain="contracts",
            k=1,
            status="ok",
            modes={"agg": {"ok": True, "ledger": bad_ledger}},
            base=[{"label": "base1", "run_id": "ref-1", "run_dir": str(run_dir)}],
            selections={"agg": selected},
        )
        schedule._write_summary(task_dir / "summary.json", res)

        buf = io.StringIO()
        with redirect_stdout(buf):
            sels = judge_cli.collect_selections(ns_dir, ("agg",))
        self.assertIn("budget identity violated (R=2/4, P=3/3)", buf.getvalue())
        self.assertFalse(sels[0]["ok"])

        self.adapter.score = Mock(return_value={"n_passed": 2, "n_criteria": 2})
        scored_rows = judge.judge_selections(self.adapter, sels)
        self.adapter.score.assert_not_called()
        self.assertFalse(scored_rows[0].graded)
        self.assertIn("budget identity violated", scored_rows[0].error)

        # Even if a stale graded judged.json row exists on disk, report surfaces a WARNING
        # and re-running cli.judge replaces the stale grade instead of preserving it.
        stale_graded = judge.Scored(
            task_id="contracts/task-1",
            mode="agg",
            k=1,
            label="agg1",
            run_id="adapted-1",
            chosen_is_base=False,
            selected_at="2026-11-24T00:00:00+00:00",
            n_passed=2,
            n_criteria=2,
            all_pass=True,
        )
        judge.write([stale_graded], ns_dir / "judged.json")

        violations = report_cli._budget_violations(ns_dir, ("agg",), 1)
        summaries = report.summarise([stale_graded], budget_violations=violations)
        rendered = report.render(summaries, title="lab-oc", bench="lab")
        self.assertIn("WARNING  parallel k=1: 1 task summary(ies) violated", rendered)
        self.assertIn("contracts/task-1: R=2/4, P=3/3", rendered)

        env = {
            key: os.environ[key]
            for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "TERM")
            if key in os.environ
        }
        env.update(
            HARNESS2_RUNS_DIR=str(self.root),
            HARNESS2_SOLVER_MODEL="fixture/executor",
            HARNESS2_JUDGE_MODEL="fixture-judge",
            PYTHONDONTWRITEBYTECODE="1",
        )
        repo_root = Path(__file__).resolve().parents[1]

        rep_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "harness2.cli.report",
                "--bench",
                "lab",
                "--k",
                "1",
                "--mode",
                "parallel",
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(rep_proc.returncode, 0, rep_proc.stdout + rep_proc.stderr)
        self.assertIn("violated the R=4k / P=3k budget identity", rep_proc.stdout)

        judge_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "harness2.cli.judge",
                "--bench",
                "lab",
                "--k",
                "1",
                "--mode",
                "parallel",
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(judge_proc.returncode, 2, judge_proc.stdout + judge_proc.stderr)
        self.assertIn("refusing to judge contracts/task-1 [agg]", judge_proc.stdout)
        persisted = json.loads((ns_dir / "judged.json").read_text(encoding="utf-8"))
        self.assertFalse(persisted[0]["graded"])
        self.assertIn("budget identity violated", persisted[0]["error"])


if __name__ == "__main__":
    unittest.main()
