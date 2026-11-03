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

"""A what-if data table renders as a stable formula with its cached value."""

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

from harness2.benchmarks import wb_render

HAVE_OPENPYXL = importlib.util.find_spec("openpyxl") is not None


def _workbook_with_data_tables(path: Path) -> None:
    """A workbook holding the data-table cells Excel writes: openpyxl cannot author them."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws["A1"] = 2
    ws["A2"] = 3
    ws["B1"] = "placeholder"
    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    cells = (
        b'<c r="C1"><f t="dataTable" ref="C1:C3" dt2D="0" dtr="0" r1="A1"/><v>20</v></c>'
        b'<c r="D1"><f t="dataTable" ref="D1:F1" dt2D="0" dtr="1" r1="A2"/><v>30</v></c>'
        b'<c r="E1"><f t="dataTable" ref="E1:G3" dt2D="1" dtr="0" r1="A1" r2="A2"/><v>60</v></c>'
        b'<c r="F1"><f t="dataTable" ref="F1:F3" dt2D="0" dtr="0" r1="A1"/></c>'
    )
    with zipfile.ZipFile(tmp) as zin, zipfile.ZipFile(path, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b"</row>", cells + b"</row>", 1)
            zout.writestr(info, data)
    tmp.unlink()


@unittest.skipUnless(HAVE_OPENPYXL, "openpyxl is not installed (the [render] extra)")
class DataTableFormulaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "whatif.xlsx"
        _workbook_with_data_tables(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self) -> list[str]:
        text = wb_render._xlsx_text(self.path, "whatif.xlsx")
        row = next(line for line in text.splitlines() if line.startswith("R1: "))
        return row[len("R1: ") :].split(" | ")

    def test_renders_the_table_and_its_cached_value(self):
        self.assertEqual(
            self._row()[2:],
            [
                "formula:=TABLE(,A1) = 20",
                "formula:=TABLE(A2,) = 30",
                "formula:=TABLE(A1,A2) = 60",
                f"formula:=TABLE(,A1) -> {wb_render._XLSX_NO_CACHE}",
            ],
        )

    def test_rendering_is_stable_across_loads(self):
        self.assertEqual(
            wb_render._xlsx_text(self.path, "whatif.xlsx"),
            wb_render._xlsx_text(self.path, "whatif.xlsx"),
        )


if __name__ == "__main__":
    unittest.main()
