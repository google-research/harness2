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

"""Image evidence survives copying from a JobBench rollout into its context."""

import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.core import context
from harness2.core.spec import RunView


def png(red: int) -> bytes:
    def chunk(kind, data):
        checksum = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes([0, red, 0, 0])))
        + chunk(b"IEND", b"")
    )


class ContextImagesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task_id = "analysts/task1"
        dependencies = self.root / "dependencies"
        location = patch.object(config, "DEPENDENCIES", dependencies)
        location.start()
        self.addCleanup(location.stop)
        task = dependencies / "job-bench-eval/dataset/main" / self.task_id / "task_folder"
        task.mkdir(parents=True)
        (task / "TASK_INSTRUCTIONS.txt").write_text("Prepare quarterly charts.", encoding="utf-8")
        self.adapter = JobBenchAdapter(store_root=self.root / "store")
        self.run = self.root / "store" / self.task_id / "model_output/agg1"
        self.run.mkdir(parents=True)

    def check_images(self, names):
        originals = []
        for index, name in enumerate(names, 1):
            image = self.run / name
            image.parent.mkdir(parents=True, exist_ok=True)
            data = png(index)
            image.write_bytes(data)
            originals.append(data)
        self.assertEqual(len(self.adapter.deliverable_images(self.run)), len(names))
        evidence = context.build(
            self.root / "context",
            self.adapter,
            self.task_id,
            [RunView("agg1", "run1", self.run, True, True)],
        )
        images = evidence.root / "deliverables/agg1/images"
        copied = [image.read_bytes() for image in images.iterdir()]
        self.assertCountEqual(copied, originals)
        self.assertEqual(len(copied), len(names))

    def test_same_basename_images_are_preserved(self):
        self.check_images(["Q1/chart.png", "Q2/chart.png"])

    def test_distinct_basename_images_are_preserved(self):
        self.check_images(["Q1/chart.png", "Q2/other.png"])

    def test_numbered_source_names_do_not_collide(self):
        self.check_images(["Q1/chart.png", "Q2/1-chart.png", "Q3/chart.png"])


if __name__ == "__main__":
    unittest.main()
