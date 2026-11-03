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

"""Answer-key globs keep matching paths out of the evidence a model reads."""

import dataclasses
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness2.core import context, schedule
from harness2.demo import DemoAdapter, DemoProposer

LEAK_GLOBS = (".answers/**", "./.env", "/expected/**", "reference\\key.txt", "./RUBRICS.json")
LEAKS = (
    ".answers/key.json",
    "sub/.answers/key.json",
    ".env",
    "sub/.env",
    "expected/out.csv",
    "reference/key.txt",
    "RUBRICS.json",
)
KEPT = ("answers/notes.md", "env", "sub/env.txt", "ledger.csv")


class LeakGlobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, root, names):
        for name in names:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(name)

    def test_tripwire_matches_dotted_and_prefixed_globs(self):
        for name in LEAKS:
            with self.subTest(name=name):
                tree = self.root / name.replace("/", "_")
                self.write(tree, (name,))
                with self.assertRaisesRegex(ValueError, "answer-key"):
                    context.assert_no_leak_paths(tree, LEAK_GLOBS)
        clean = self.root / "clean"
        self.write(clean, KEPT)
        context.assert_no_leak_paths(clean, LEAK_GLOBS)

    def test_copy_source_skips_dotted_answer_keys(self):
        src = self.root / "task"
        self.write(src, KEPT + LEAKS)
        adapter = SimpleNamespace(
            task_files=lambda task_id: src, spec=SimpleNamespace(leak_globs=LEAK_GLOBS)
        )
        dest = self.root / "context"
        self.assertEqual(context.copy_source(adapter, "t", dest), len(KEPT))
        source = dest / "source"
        copied = sorted(p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file())
        self.assertEqual(copied, sorted(KEPT))

    def test_dotted_answer_key_never_reaches_a_run(self):
        adapter = DemoAdapter(self.root)
        adapter.spec = dataclasses.replace(adapter.spec, leak_globs=(".answers/**",))
        for task_id in adapter.tasks():
            self.write(adapter.task_files(task_id), (".answers/key.json", "answers/notes.md"))
        schedule.run_domain(
            "accounting",
            adapter.tasks(),
            1,
            adapter=adapter,
            prop=DemoProposer(adapter.spec.channels["proposer"]),
            runs_root=adapter.spec.runs_root,
            library_root=self.root / "library",
        )
        runs = adapter.spec.runs_root
        self.assertEqual(list(runs.rglob("key.json")), [])
        self.assertTrue(list(runs.rglob("source/answers/notes.md")))


if __name__ == "__main__":
    unittest.main()
