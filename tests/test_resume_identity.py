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

"""Regressions for re-running tasks on resume when prompts or the library change."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2.core import schedule
from harness2.core.library import Library
from harness2.demo import DemoAdapter, DemoProposer


class ResumeIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DemoAdapter(self.root)
        self.proposer = DemoProposer(self.adapter.spec.channels["proposer"])

    def run_stream(self, k=2, proposer=None):
        return schedule.run_domain(
            "accounting",
            self.adapter.tasks(),
            k,
            adapter=self.adapter,
            prop=proposer or self.proposer,
            runs_root=self.adapter.spec.runs_root,
            library_root=self.root / "library" / str(k),
        )

    def test_prompts_digest_resume(self):
        from harness2.core import prompts

        first = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in first["tasks"]], ["ok", "ok"])
        second = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in second["tasks"]], ["skipped", "skipped"])

        with patch.object(prompts, "ANSWER_FREE_SELECT", prompts.ANSWER_FREE_SELECT + "\nEDITED."):
            edited_sel = self.run_stream(k=2)
            self.assertEqual([r["status"] for r in edited_sel["tasks"]], ["ok", "ok"])
            resumed_sel = self.run_stream(k=2)
            self.assertEqual([r["status"] for r in resumed_sel["tasks"]], ["skipped", "skipped"])

        with patch.object(prompts, "ANSWER_FREE", prompts.ANSWER_FREE + "\nEDITED."):
            edited_exp = self.run_stream(k=2)
            self.assertEqual([r["status"] for r in edited_exp["tasks"]], ["ok", "ok"])
        restored = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in restored["tasks"]], ["ok", "ok"])

        summary_path = self.adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json"
        doc = json.loads(summary_path.read_text())
        del doc["selections"]["agg"]["prompts_digest"]
        summary_path.write_text(json.dumps(doc))
        upgraded = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in upgraded["tasks"]], ["ok", "skipped"])

        first_k1 = self.run_stream(k=1)
        self.assertEqual([r["status"] for r in first_k1["tasks"]], ["ok", "ok"])
        with patch.object(prompts, "ANSWER_FREE_SELECT", prompts.ANSWER_FREE_SELECT + "\nEDITED."):
            sel_only_k1 = self.run_stream(k=1)
            self.assertEqual([r["status"] for r in sel_only_k1["tasks"]], ["skipped", "skipped"])
        with patch.object(prompts, "ANSWER_FREE", prompts.ANSWER_FREE + "\nEDITED."):
            exp_k1 = self.run_stream(k=1)
            self.assertEqual([r["status"] for r in exp_k1["tasks"]], ["ok", "ok"])

        assets_adapter = DemoAdapter(self.root / "with_assets")
        assets_adapter.deliverable_assets = lambda rd: [Path(rd) / "answer.txt"]
        for expected in (["ok", "ok"], ["skipped", "skipped"]):
            res_assets = schedule.run_domain(
                "accounting",
                assets_adapter.tasks(),
                2,
                adapter=assets_adapter,
                prop=self.proposer,
                runs_root=assets_adapter.spec.runs_root,
                library_root=self.root / "with_assets/library",
            )
            self.assertEqual([r["status"] for r in res_assets["tasks"]], expected)

    def test_prompts_digest_survives_scratch_and_entry_point(self):
        import shutil

        from harness2.core import selection

        self.run_stream(k=2)
        sel_dirs = list(self.adapter.spec.runs_root.glob("*/*/selection"))
        self.assertTrue(sel_dirs)
        for sel_dir in sel_dirs:
            shutil.rmtree(sel_dir)
        pruned = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in pruned["tasks"]], ["skipped", "skipped"])

        header = selection._candidate_header
        with patch.object(selection, "_candidate_header", lambda *a: header(*a) + "EDITED"):
            edited = self.run_stream(k=2)
            self.assertEqual([r["status"] for r in edited["tasks"]], ["ok", "ok"])
        restored = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in restored["tasks"]], ["ok", "ok"])

        first = self.adapter.tasks()[0]
        solo = schedule.run_task(
            first,
            3,
            adapter=self.adapter,
            prop=self.proposer,
            runs_root=self.adapter.spec.runs_root,
        )
        self.assertEqual(solo.status, "ok")
        streamed = self.run_stream(k=3)
        self.assertEqual([r["status"] for r in streamed["tasks"]], ["skipped", "ok"])

    def test_library_change_invalidates_later_tasks(self):
        self.run_stream(k=2)
        summary_path = self.adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json"
        doc = json.loads(summary_path.read_text())
        for sel in doc["selections"].values():
            sel["experience"]["lesson"] += " (revised)"
        summary_path.write_text(json.dumps(doc))
        resumed = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in resumed["tasks"]], ["skipped", "ok"])
        again = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in again["tasks"]], ["skipped", "skipped"])

    def test_prompts_digest_failure_keeps_selection(self):
        from harness2.core import selection

        boom = Mock(side_effect=TypeError("adapter bug"))
        with patch.object(selection, "prompts_digest", boom):
            broken = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in broken["tasks"]], ["ok", "ok"])
        summary_path = self.adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json"
        doc = json.loads(summary_path.read_text())
        for sel in doc["selections"].values():
            self.assertTrue(sel["ok"] and sel["chosen_label"])
            self.assertEqual(sel["prompts_digest"], "")
        surface = self.adapter.substrate.surface()
        self.assertTrue(Library(self.root / "library/2/agg", "accounting", surface).entries())

        fixed = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in fixed["tasks"]], ["ok", "ok"])
        with patch.object(selection, "prompts_digest", boom):
            resumed = self.run_stream(k=2)
            self.assertEqual([r["status"] for r in resumed["tasks"]], ["ok", "ok"])
            still_broken = self.run_stream(k=2)
        self.assertEqual([r["status"] for r in still_broken["tasks"]], ["skipped", "skipped"])

    def test_own_library_entry_does_not_block_resume(self):
        first = self.adapter.tasks()[0]
        lib = Library(self.root / "solo-library", "accounting", self.adapter.substrate.surface())
        statuses = []
        for _ in range(2):
            res = schedule.run_task(
                first,
                2,
                adapter=self.adapter,
                prop=self.proposer,
                runs_root=self.adapter.spec.runs_root,
                modes=("agg",),
                library=lib,
            )
            statuses.append(res.status)
            self.assertTrue(lib.entries())
        self.assertEqual(statuses, ["ok", "skipped"])

    def test_prompts_digest_covers_per_candidate_text(self):
        from harness2.core import selection

        task = self.adapter.tasks()[0]
        rec = {"n_candidates": 2, "pool_kind": "finals", "asset_names": {"A": ["x.png", "y.png"]}}

        def digest(**changes):
            return selection.prompts_digest(
                task, self.adapter, {**rec, **changes}, experience=False
            )

        self.assertNotEqual(digest(), digest(asset_names={"A": ["y.png", "x.png"]}))
        self.assertNotEqual(digest(), digest(asset_names={"B": ["x.png", "y.png"]}))
        transcript = digest(evidence="trajectory")
        self.assertNotEqual(digest(), transcript)
        with patch.object(selection, "_TRAJECTORY_PREAMBLE", "(edited)\n\n"):
            self.assertNotEqual(transcript, digest(evidence="trajectory"))

    def test_experience_prompted_follows_reflection(self):
        from harness2.core import selection

        sel = selection.Selection(task_id="t", mode="agg", k=2, ok=True, chosen_label="gone")
        out = selection.reflect_adoption(
            "t", sel, {}, adapter=self.adapter, prop=self.proposer, work_dir=self.root
        )
        self.assertEqual(out, {})
        self.assertFalse(sel.experience_prompted)

        self.run_stream(k=2)
        summary_path = self.adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json"
        doc = json.loads(summary_path.read_text())
        self.assertTrue(all(s["experience_prompted"] for s in doc["selections"].values()))


if __name__ == "__main__":
    unittest.main()
