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

"""The raw-reads fallback must name images, not decode their bytes into the text view."""

import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.jobbench import JobBenchAdapter

# A few bytes from a real PNG header; not valid UTF-8.
PNG_BYTES = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0xFF, 0xFE])


class RawExtractImageTests(unittest.TestCase):
    def _dir(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        d = Path(temporary.name)
        (d / "chart.png").write_bytes(PNG_BYTES)
        (d / "notes.txt").write_text("the real deliverable", encoding="utf-8")
        return d

    def test_image_is_named_not_decoded(self):
        got = JobBenchAdapter._raw_extract(self._dir())

        # The image is listed as an image path.
        self.assertEqual([Path(p).name for p in got["images"]], ["chart.png"])

        # Its files entry and the flat view must NOT carry the decoded image bytes
        # (U+FFFD replacement chars), which are noise to the proposer/selector.
        png_text = got["files"]["chart.png"]
        self.assertNotIn("�", png_text)
        self.assertNotIn("�", got["flat"])
        self.assertTrue(png_text.startswith("(image"), png_text)

        # The genuine text deliverable is untouched.
        self.assertEqual(got["files"]["notes.txt"], "the real deliverable")
        self.assertIn("the real deliverable", got["flat"])


if __name__ == "__main__":
    unittest.main()
