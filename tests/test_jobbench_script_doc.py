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

"""The helper-script summary in the agent body comes from the module docstring."""

import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.jobbench import _script_doc


class ScriptDocTests(unittest.TestCase):
    def _write(self, text: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        p = Path(temporary.name) / "helper.py"
        p.write_text(text, encoding="utf-8")
        return p

    def test_docstring_after_license_header(self):
        # Every committed script in this repo opens with the Apache header, so the
        # docstring is never the first byte of the file.
        text = (
            "# Copyright 2026 Google LLC\n"
            "#\n"
            "# Licensed under the Apache License, Version 2.0 (the \"License\");\n"
            '"""Normalise a CSV file in place."""\n'
            "import sys\n"
        )
        self.assertEqual(_script_doc(self._write(text)), "Normalise a CSV file in place.")

    def test_docstring_after_shebang(self):
        text = '#!/usr/bin/env python3\n"""Convert a workbook to JSON."""\n'
        self.assertEqual(_script_doc(self._write(text)), "Convert a workbook to JSON.")

    def test_single_line_docstring_has_no_trailing_quotes(self):
        # A docstring opened and closed on one line must not carry the closing quotes
        # into the summary line.
        text = '"""Deduplicate rows."""\n'
        self.assertEqual(_script_doc(self._write(text)), "Deduplicate rows.")

    def test_first_line_of_multiline_docstring(self):
        text = '"""Split a ledger.\n\nLonger explanation that must not appear.\n"""\n'
        self.assertEqual(_script_doc(self._write(text)), "Split a ledger.")

    def test_no_docstring_falls_back(self):
        text = "# Copyright 2026 Google LLC\nimport sys\n"
        self.assertEqual(_script_doc(self._write(text)), "helper script")


if __name__ == "__main__":
    unittest.main()
