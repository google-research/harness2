# SPDX-License-Identifier: Apache-2.0

"""Preserve distinct filesystem artifacts in the proposer's evidence tree."""

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.core import context
from harness2.core.spec import RunView


class ContextArtifactTests(unittest.TestCase):
    def build_from_files(self, names):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        task = root / "dataset/main/accounting/task1/task_folder"
        task.mkdir(parents=True)
        (task / "TASK_INSTRUCTIONS.txt").write_text("Read the supplied reports.")
        adapter = JobBenchAdapter(store_root=root / "store")
        run = root / "store/accounting/task1/model_output/base1"
        run.mkdir(parents=True)
        expected = {}
        for index, name in enumerate(names):
            path = run / name
            path.parent.mkdir(parents=True, exist_ok=True)
            expected[name] = f"Distinct report {index}: {name}\n"
            path.write_text(expected[name], encoding="utf-8")
        with patch.object(config, "jb_repo", return_value=root):
            # Exercise the real benchmark reader's documented raw-file fallback.
            self.assertEqual(adapter.deliverable_files(run), expected)
            context.build(
                root / "context",
                adapter,
                "accounting/task1",
                [RunView("base1", "local/base1", run, True, True)],
            )
        return root / "context/deliverables/base1", expected

    def assert_all_reports_preserved(self, names):
        directory, expected = self.build_from_files(names)
        rendered = list(directory.glob("*.txt"))
        self.assertEqual(len(rendered), len(expected))
        self.assertCountEqual(
            [path.read_text(encoding="utf-8") for path in rendered], expected.values()
        )
        index = (directory / "INDEX.md").read_text(encoding="utf-8")
        for name, body in expected.items():
            line = next(line for line in index.splitlines() if line.startswith(f"- `{name}` "))
            match = re.search(r"\]\(([^)]+)\)", line)
            self.assertIsNotNone(match, line)
            self.assertEqual((directory / match[1]).read_text(encoding="utf-8"), body)

    def test_nested_and_flat_names_do_not_overwrite(self):
        self.assert_all_reports_preserved(["reports/answer.txt", "reports__answer.txt"])

    def test_extensionless_and_text_names_do_not_overwrite(self):
        self.assert_all_reports_preserved(["report", "report.txt"])

    def test_single_artifact_control(self):
        directory, expected = self.build_from_files(["ordinary.txt"])
        rendered = list(directory.glob("*.txt"))
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0].read_text(encoding="utf-8"), expected["ordinary.txt"])


if __name__ == "__main__":
    unittest.main()
