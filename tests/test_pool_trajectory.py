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

"""A pool candidate's trajectory survives a deliverable-rendering failure."""

import tempfile
import unittest
from pathlib import Path

from harness2.core import context
from harness2.core.spec import BenchPrompts, BenchSpec, ChannelSpec, RunView
from harness2.substrate.opencode import OpencodeSubstrate


class _Adapter:
    """The adapter surface context.build reads: a trajectory that renders and a
    deliverable that raises, the LAB extraction-failure shape."""

    substrate = OpencodeSubstrate()
    spec = BenchSpec(
        name="pooltest",
        runs_root=Path("."),
        install_mode="workspace_agents_md",
        prompts=BenchPrompts(work="test"),
        channels={
            "proposer": ChannelSpec("opencode", "local/test"),
            "selector": ChannelSpec("opencode", "local/test"),
        },
    )

    def task_prompt(self, task_id):
        return f"prompt for {task_id}"

    def trajectory(self, run_dir, *, obs_trunc):
        return "[ACTION] the full trajectory"

    def deliverable_text(self, run_dir):
        raise RuntimeError("deliverable extraction failed")


class PoolTrajectoryTests(unittest.TestCase):
    def test_pool_trajectory_survives_deliverable_failure(self):
        with tempfile.TemporaryDirectory() as td:
            roll = RunView(
                label="agg1", run_id="r1", run_dir=Path(td) / "run", ok=True, delivered=True
            )
            tree = context.build(
                Path(td) / "ctx",
                _Adapter(),
                "task-1",
                pool=[("agg1", Path(td) / "cand", roll)],
            )
            self.assertIn("agg1", tree.pool)
            rollout = (Path(td) / "ctx" / "pool" / "agg1" / "rollout.md").read_text()
            self.assertIn("[ACTION] the full trajectory", rollout)
            self.assertNotIn("unavailable", rollout)
            produced = (Path(td) / "ctx" / "pool" / "agg1" / "produced.md").read_text()
            self.assertIn("deliverable extraction failed", produced)

    def test_pool_trajectory_failure_still_records_deliverable(self):
        class _TrajectoryFails(_Adapter):
            def trajectory(self, run_dir, *, obs_trunc):
                raise RuntimeError("trajectory renderer failed")

            def deliverable_text(self, run_dir):
                return "the artifact"

        with tempfile.TemporaryDirectory() as td:
            roll = RunView(
                label="agg1", run_id="r1", run_dir=Path(td) / "run", ok=True, delivered=True
            )
            context.build(
                Path(td) / "ctx",
                _TrajectoryFails(),
                "task-1",
                pool=[("agg1", Path(td) / "cand", roll)],
            )
            rollout = (Path(td) / "ctx" / "pool" / "agg1" / "rollout.md").read_text()
            self.assertIn("trajectory renderer failed", rollout)
            produced = (Path(td) / "ctx" / "pool" / "agg1" / "produced.md").read_text()
            self.assertIn("the artifact", produced)


if __name__ == "__main__":
    unittest.main()
