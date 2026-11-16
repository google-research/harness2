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

"""Placeholders for missing evidence must not be treated as a rollout."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness2.core import context
from harness2.core.selection import Selection, _render_pool
from harness2.core.spec import RunView


class AbsentEvidenceTests(unittest.TestCase):
    def test_predicate_recognises_every_placeholder(self):
        for text in (
            "",
            "   ",
            "(no trajectory)",
            "(no trajectory for task @ base)",
            "(no trajectory recorded for run)",
            "(trajectory unavailable: OSError: gone)",
            "(deliverable unavailable: ValueError: bad)",
            "(no deliverable produced)",
            "(nothing produced)",
            "(unavailable: timeout)",
            "(listing unavailable: RuntimeError: no)",
        ):
            self.assertTrue(context.is_absent_evidence(text), text)
        self.assertFalse(context.is_absent_evidence("the agent wrote the memo"))

    def test_pool_keeps_a_real_transcript_and_drops_placeholders(self):
        bodies = {
            "missing": "(trajectory unavailable: OSError: gone)",
            "empty": "(nothing produced)",
            "adapter": "(no trajectory)",
            "real": "observed the ledger and wrote the total",
        }
        adapter = SimpleNamespace(
            spec=SimpleNamespace(state_deliverable=True, obs_trunc=50_000),
            deliverable_text=lambda run_dir: "",
            trajectory=lambda run_dir, obs_trunc=0: bodies[Path(run_dir).name],
        )
        candidates = {
            name: RunView(name, name, Path(name), True, False) for name in bodies
        }
        sel = Selection(task_id="t", mode="seq", k=1)
        pool, artifacts = _render_pool(candidates, adapter, sel)
        self.assertEqual(set(pool), {"real"})
        self.assertIn("ledger", artifacts["real"])
        self.assertEqual(sel.evidence, "trajectory")

    def test_context_does_not_serve_a_placeholder_trajectory(self):
        bodies = {
            "base": "(no trajectory for t @ base)",
            "kept": "read the ledger and wrote the total",
        }
        adapter = SimpleNamespace(
            spec=SimpleNamespace(
                obs_trunc=50_000,
                leak_globs=(),
                proposer_deliverables="contents",
            ),
            task_prompt=lambda task_id: "add the column",
            trajectory=lambda run_dir, obs_trunc=0: bodies[Path(run_dir).name],
            deliverable_text=lambda run_dir: "Total: 10",
        )
        rolls = [RunView(name, name, Path(name), True, True) for name in ("base", "kept")]
        with tempfile.TemporaryDirectory() as tmp:
            tree = context.build(Path(tmp) / "context", adapter, "t", rolls)
            self.assertEqual(tree.rollouts, ["kept"])
            self.assertTrue(any(item.startswith("base:") for item in tree.failed))
            self.assertFalse((tree.root / "rollouts" / "base.md").exists())
            self.assertIn("ledger", (tree.root / "rollouts" / "kept.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
