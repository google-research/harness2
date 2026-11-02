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

"""Raw deliverable assets that share a basename are all copied into model context.

A WorkBuddy office trial can write `q1/report.xlsx` and `q2/report.xlsx`. The rendered text
keeps both names, and so must the raw originals copied beside it: for the improver under
`context/deliverables/<label>/raw/`, for the selector under `candidates/<id>/`.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks import wb_render
from harness2.benchmarks.workbuddy import WbAdapter
from harness2.core import context, selection
from harness2.core.proposer import Reply
from harness2.core.spec import RunView

TASK = "quarterly-report"
WORKBOOKS = {"q1/report.xlsx": b"north region", "q2/report.xlsx": b"south region"}


class ChooseFirst:
    """A selector that picks candidate A without reading anything."""

    def call(self, mission, context_dir, label, **kwargs):
        return Reply(label, "```choice\nA\n```", True, attempts=1)


class AssetBasenameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        task = self.root / "wb/datasets/wb-bench-office-v1.0/tasks" / TASK
        task.mkdir(parents=True)
        (task / "task.toml").write_text('[metadata]\ncategory = "data-file-ops"\n')
        (task / "instruction.md").write_text("Write one report workbook per quarter.")
        self.trial = self.root / "trial"
        for name, body in WORKBOOKS.items():
            path = self.trial / "verifier/raw_artifacts" / name
            path.parent.mkdir(parents=True)
            path.write_bytes(body)
        # openpyxl is an optional extra: the workbook text comes from a stand-in, while the
        # raw files still come from the real office trial layout.
        for p in (
            patch.object(config, "wb_repo", return_value=self.root / "wb"),
            patch.object(
                wb_render,
                "_render_binaries",
                lambda trial, subset, items: {key: path.read_text() for key, path, _ in items},
            ),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.adapter = WbAdapter("office", substrate="oc")
        self.run = RunView(label="agg1", run_id="r1", run_dir=self.trial, ok=True, delivered=True)

    def assert_workbooks(self, folder: Path):
        got = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*.xlsx")}
        self.assertEqual(got, WORKBOOKS)

    def test_improver_context_keeps_every_raw_workbook(self):
        context.build(self.root / "context", self.adapter, TASK, [self.run])
        self.assert_workbooks(self.root / "context/deliverables/agg1/raw")

    def test_selector_candidate_keeps_every_raw_workbook(self):
        sel = selection.run_selection(
            TASK,
            1,
            "agg",
            {"agg1": self.run},
            adapter=self.adapter,
            prop=ChooseFirst(),
            work_dir=self.root / "work",
        )
        self.assertTrue(sel.ok)
        candidates = self.root / "work/selection/agg1/candidates"
        self.assert_workbooks(candidates / "A")
        self.assertIn("`A/`: q1/report.xlsx, q2/report.xlsx.", (candidates / "A.md").read_text())

    def test_distinct_basenames_are_still_copied_flat(self):
        raw = self.trial / "verifier/raw_artifacts"
        (raw / "q2/report.xlsx").rename(raw / "q2/summary.xlsx")
        context.build(self.root / "context", self.adapter, TASK, [self.run])
        copied = self.root / "context/deliverables/agg1/raw"
        self.assertEqual(sorted(p.name for p in copied.iterdir()), ["report.xlsx", "summary.xlsx"])


if __name__ == "__main__":
    unittest.main()
