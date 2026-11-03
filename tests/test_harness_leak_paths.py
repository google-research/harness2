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

"""A harness whose paths match an answer-key glob is rejected before it can sink a mode."""

import dataclasses
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from harness2.core import context, schedule
from harness2.demo import DemoAdapter, DemoProposer


def add_skill(harness: Path, name: str) -> None:
    skill = harness / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(f"---\nname: {name}\ndescription: use {name}\n---\nBody.\n")


class NamesSkillTests(DemoProposer):
    """An editor that adds a skill named like an answer-key directory."""

    def call(self, mission, context_dir, label, **kwargs):
        reply = super().call(mission, context_dir, label, **kwargs)
        if label.startswith("inv1"):
            add_skill(reply.workspace, "tests")
        return reply


class RenamesAfterFeedback(DemoProposer):
    """An editor that renames the skill once the rejection reaches it."""

    feedback = ""

    def call(self, mission, context_dir, label, **kwargs):
        reply = super().call(mission, context_dir, label, **kwargs)
        if label == "inv1.a1":
            add_skill(reply.workspace, "tests")
        elif label == "inv1.a2":
            self.feedback = mission
            shutil.rmtree(reply.workspace / "skills/tests")
            add_skill(reply.workspace, "check_totals")
        return reply


class HarnessLeakPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.adapter.spec = dataclasses.replace(self.adapter.spec, leak_globs=("tests/**",))

    def run_stream(self, proposer_type):
        proposer = proposer_type(self.adapter.spec.channels["proposer"])
        result = schedule.run_domain(
            "accounting",
            self.adapter.tasks(),
            1,
            adapter=self.adapter,
            prop=proposer,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "library",
        )
        return proposer, result

    def summary(self, task_id):
        path = self.adapter.spec.runs_root / "demo-oc-agg1" / task_id / "summary.json"
        return json.loads(path.read_text())

    def test_tree_check_matches_the_directory_tripwire(self):
        globs = ("tests/**", "reference/**", "RUBRICS.json")
        for rel in ("skills/tests/SKILL.md", "sub_agents/reference/AGENT.md"):
            with self.subTest(rel=rel), self.assertRaisesRegex(ValueError, "rename"):
                context.assert_tree_has_no_leak_paths({rel: ""}, globs)
        context.assert_tree_has_no_leak_paths(
            {"skills/run_tests/SKILL.md": "", "scripts/tests.py": ""}, globs
        )

    def test_colliding_edit_falls_back_and_the_modes_still_finish(self):
        _, result = self.run_stream(NamesSkillTests)
        self.assertEqual([row["status"] for row in result["tasks"]], ["ok", "ok"])
        for task_id in self.adapter.tasks():
            summary = self.summary(task_id)
            for mode in ("agg", "seq"):
                self.assertEqual(summary["modes"][mode]["fell_back"], ["inv1"])
                self.assertTrue(summary["selections"][mode]["chosen_label"])

    def test_retry_is_told_which_path_to_rename(self):
        proposer, result = self.run_stream(RenamesAfterFeedback)
        self.assertEqual([row["status"] for row in result["tasks"]], ["ok", "ok"])
        self.assertIn("skills/tests/SKILL.md", proposer.feedback)
        self.assertIn("rename the component", proposer.feedback)
        harness = self.adapter.spec.runs_root / "demo-oc-agg1/ledger-1/harnesses/inv1"
        self.assertTrue((harness / "skills/check_totals/SKILL.md").is_file())
        self.assertFalse((harness / "skills/tests").exists())


if __name__ == "__main__":
    unittest.main()
