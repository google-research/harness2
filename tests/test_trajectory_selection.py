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

"""Exercise trajectory-only adoption and resume through the public scheduler."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from harness2.core import schedule
from harness2.demo import DemoAdapter, DemoProposer


class StateAdapter(DemoAdapter):
    def __init__(self, root, *, state_deliverable=True, trajectory_text=None):
        super().__init__(root)
        self.spec = replace(self.spec, state_deliverable=state_deliverable)
        self.trajectory_text = trajectory_text

    def execute(self, *args, **kwargs):
        run_dir = super().execute(*args, **kwargs)
        # The actual local script output remains in the trajectory, with no file answer.
        (run_dir / "answer.txt").unlink()
        return run_dir

    def deliverable_text(self, run_dir):
        return ""

    def deliverable_files(self, run_dir):
        return {}

    def trajectory(self, run_dir, *, obs_trunc):
        if self.trajectory_text is not None:
            return self.trajectory_text
        return super().trajectory(run_dir, obs_trunc=obs_trunc)


class TrajectorySelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_trajectory_adoption_reflection_and_resume(self):
        for k in (1, 2):
            with self.subTest(k=k):
                adapter = StateAdapter(self.root / str(k))
                proposer = DemoProposer(adapter.spec.channels["proposer"])
                arguments = dict(
                    adapter=adapter,
                    prop=proposer,
                    runs_root=adapter.spec.runs_root,
                    library_root=adapter.root / "library",
                )
                first = schedule.run_domain("accounting", adapter.tasks(), k, **arguments)
                self.assertEqual((first["done"], first["failed"]), (2, 0))
                self.assertEqual([row["status"] for row in first["tasks"]], ["ok", "ok"])
                self.assertEqual(first["tasks"][1]["library_entries_before"], {"agg": 1, "seq": 1})
                for task_id in adapter.tasks():
                    summary = json.loads(
                        (
                            adapter.spec.runs_root / f"demo-oc-agg{k}" / task_id / "summary.json"
                        ).read_text()
                    )
                    self.assertEqual(summary["status"], "ok")
                    self.assertEqual(summary["errors"], [])
                    for mode in ("agg", "seq"):
                        selected = summary["selections"][mode]
                        self.assertTrue(summary["modes"][mode]["ok"])
                        self.assertTrue(selected["ok"])
                        self.assertEqual(selected["evidence"], "trajectory")
                        self.assertEqual(selected["trivial"], k == 1)
                        self.assertTrue(selected["experience"])
                        self.assertFalse(adapter.delivered(Path(selected["chosen_run_dir"])))
                executions, calls = adapter.execution_count, len(proposer._calls)
                resumed = schedule.run_domain("accounting", adapter.tasks(), k, **arguments)
                self.assertEqual([row["status"] for row in resumed["tasks"]], ["skipped"] * 2)
                self.assertEqual(
                    (adapter.execution_count, len(proposer._calls)), (executions, calls)
                )

                # A disappeared adopted trajectory must still trigger a real rerun.
                summary = json.loads(
                    (adapter.spec.runs_root / f"demo-oc-agg{k}/ledger-1/summary.json").read_text()
                )
                chosen = Path(summary["selections"]["seq"]["chosen_run_dir"])
                (chosen / "trajectory.md").unlink()
                repaired = schedule.run_domain("accounting", adapter.tasks(), k, **arguments)
                self.assertEqual([row["status"] for row in repaired["tasks"]], ["ok", "skipped"])
                self.assertGreater(adapter.execution_count, executions)
                self.assertGreater(len(proposer._calls), calls)

    def test_unsupported_or_unavailable_trajectory_is_not_adopted(self):
        cases = (
            {"state_deliverable": False},
            {"trajectory_text": ""},
            {"trajectory_text": "(no trajectory)"},
        )
        for k in (1, 2):
            for index, settings in enumerate(cases):
                with self.subTest(k=k, settings=settings):
                    adapter = StateAdapter(self.root / f"{k}-{index}", **settings)
                    proposer = DemoProposer(adapter.spec.channels["proposer"])
                    result = schedule.run_task(
                        "ledger-1",
                        k,
                        adapter=adapter,
                        prop=proposer,
                        runs_root=adapter.spec.runs_root,
                    )
                    self.assertEqual(result.status, "partial")
                    self.assertTrue(result.errors)
                    self.assertEqual(set(result.selections), {"agg", "seq"})
                    for selected in result.selections.values():
                        self.assertFalse(selected["ok"])
                        self.assertEqual(selected["chosen_label"], "")
                        self.assertEqual(selected["n_candidates"], 0)
                        self.assertIn("no candidate produced an artifact", selected["error"])


if __name__ == "__main__":
    unittest.main()
