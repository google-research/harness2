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

"""An office artifact that its reader cannot open is evidence, not the end of the rollout.

An agent can write CSV text under a `.xlsx` name. openpyxl rejects that file; when the error
escapes `wb_render`, `WbAdapter.delivered()` raises and the scheduler records the whole trial,
with its other deliverables and its trajectory, as a dead rollout.
"""

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks import wb_render
from harness2.benchmarks.workbuddy import WbAdapter
from harness2.core import schedule

MEMO = "# Memo\n" + "Quarterly totals reconcile to the ledger. " * 10
CSV = b"region,total\nnorth,10\n"


def not_a_workbook(path, name):
    """What openpyxl raises for a file that is not a zip package."""
    raise zipfile.BadZipFile("File is not a zip file")


def reader_not_installed(path, name):
    raise ModuleNotFoundError("No module named 'openpyxl'")


class UnreadableBinaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.trial = self.root / "trial"
        raw = self.trial / "verifier/raw_artifacts"
        raw.mkdir(parents=True)
        (raw / "memo.md").write_text(MEMO)
        (raw / "totals.xlsx").write_bytes(CSV)
        self.adapter = WbAdapter("office", substrate="oc")

    @staticmethod
    def reader(fn):
        # openpyxl is an optional extra; the stand-in raises what openpyxl raises here.
        return patch.dict(wb_render._BINARY_HANDLERS, {".xlsx": ("zipfile", fn)})

    def test_unreadable_workbook_is_shown_beside_the_other_deliverables(self):
        with self.reader(not_a_workbook):
            text = self.adapter.deliverable_text(self.trial)
            self.assertTrue(self.adapter.delivered(self.trial))
        self.assertIn(MEMO, text)
        self.assertIn(
            f"## totals.xlsx\n(.xlsx file, {len(CSV)} bytes -- unreadable: "
            f"BadZipFile: File is not a zip file)",
            text,
        )

    def test_unreadable_workbook_keeps_the_base_rollout(self):
        with (
            self.reader(not_a_workbook),
            patch.object(self.adapter, "execute", return_value=self.trial),
        ):
            got = schedule._roll_bases(
                "t1", self.adapter, [("base1", "wb-office-oc-agg1/t1/base1")], self.root / "dead"
            )
        base = got["base1"]
        self.assertTrue(base.ok, base.error)
        self.assertEqual(base.run_dir, self.trial)
        self.assertTrue(base.delivered)

    def test_unreadable_workbook_in_one_replicate_keeps_the_whole_batch(self):
        good = self.root / "good"
        (good / "verifier/raw_artifacts").mkdir(parents=True)
        (good / "verifier/raw_artifacts/memo.md").write_text(MEMO)
        pending = [("base1", "wb-office-oc-agg2/t1/base1"), ("base2", "wb-office-oc-agg2/t1/base2")]
        with (
            self.reader(not_a_workbook),
            patch.object(self.adapter, "execute_many", return_value=[good, self.trial]),
        ):
            got = schedule._roll_bases("t1", self.adapter, pending, self.root / "dead")
        self.assertEqual([got[label].run_dir for label, _ in pending], [good, self.trial])
        self.assertTrue(all(got[label].ok and got[label].delivered for label, _ in pending))

    def test_missing_reader_still_fails_loudly(self):
        with self.reader(reader_not_installed), self.assertRaises(ImportError):
            self.adapter.delivered(self.trial)


if __name__ == "__main__":
    unittest.main()
