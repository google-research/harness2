# SPDX-License-Identifier: Apache-2.0
"""Exercise actual trajectory lookup with configurable store roots."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.jobbench import JobBenchAdapter


class JobBenchRunPathTests(unittest.TestCase):
    def test_trajectory_survives_model_output_in_store_root_or_run_label(self):
        with tempfile.TemporaryDirectory() as td:
            for root_name in ("ordinary", "model_output/store"):
                for label in ("run-1", "model_output"):
                    with self.subTest(root=root_name, label=label):
                        store = Path(td) / root_name
                        adapter = JobBenchAdapter("cx", store_root=store)
                        run = store / "accounting/task1/model_output" / label
                        run.mkdir(parents=True, exist_ok=True)
                        trajectory = store / "accounting/task1/model_traj" / label
                        trajectory.mkdir(parents=True, exist_ok=True)
                        (trajectory / "events.jsonl").write_text(
                            json.dumps(
                                {
                                    "type": "item.completed",
                                    "item": {
                                        "id": "item_0",
                                        "type": "agent_message",
                                        "text": "Prepared the requested ledger.",
                                    },
                                }
                            )
                            + "\n"
                        )
                        self.assertIn(
                            "Prepared the requested ledger.",
                            adapter.trajectory(run, obs_trunc=1000),
                        )

    def test_non_run_directory_is_rejected(self):
        adapter = JobBenchAdapter("cx")
        with self.assertRaises(ValueError):
            adapter.trajectory(Path("model_output/not-a-run/nested/file"), obs_trunc=1000)
