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

"""Task inputs are part of a rollout's identity, at both cache layers.

A rollout answers the task it was given. When the instruction or the source material changes
under the same task id, the saved answer is no longer an answer to the current question, so
neither the task summary nor the rollout marker may be reused.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks.lab import LabAdapter
from harness2.core import runmark, schedule


class _FakeAdapter:
    """The two adapter methods the task-input identity reads, and nothing else."""

    def __init__(self, prompt: str, files: Path | None):
        self._prompt = prompt
        self._files = files

    def task_prompt(self, task_id: str) -> str:
        return self._prompt

    def task_files(self, task_id: str) -> Path | None:
        return self._files


class InputsFingerprintTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = self.root / "documents"
        self.files.mkdir()
        (self.files / "source.txt").write_text("version-one")

    def test_digest_is_stable_for_unchanged_inputs(self):
        first = runmark.inputs_fingerprint("Use version one.", self.files)
        second = runmark.inputs_fingerprint("Use version one.", self.files)
        self.assertEqual(first, second)

    def test_digest_follows_the_prompt(self):
        before = runmark.inputs_fingerprint("Use version one.", self.files)
        after = runmark.inputs_fingerprint("Use version two.", self.files)
        self.assertNotEqual(before, after)

    def test_digest_follows_file_contents_and_membership(self):
        before = runmark.inputs_fingerprint("Use version one.", self.files)
        (self.files / "source.txt").write_text("version-two")
        changed = runmark.inputs_fingerprint("Use version one.", self.files)
        self.assertNotEqual(before, changed)
        (self.files / "extra.txt").write_text("more")
        added = runmark.inputs_fingerprint("Use version one.", self.files)
        self.assertNotEqual(changed, added)

    def test_digest_ignores_byproducts_and_missing_material(self):
        before = runmark.inputs_fingerprint("Use version one.", self.files)
        (self.files / "__pycache__").mkdir()
        (self.files / "__pycache__" / "stale.pyc").write_bytes(b"\x00")
        self.assertEqual(before, runmark.inputs_fingerprint("Use version one.", self.files))
        self.assertEqual(
            runmark.inputs_fingerprint("Use version one.", None),
            runmark.inputs_fingerprint("Use version one.", self.root / "absent"),
        )


class MarkerInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name) / "run"

    def test_marker_refuses_reuse_under_changed_inputs(self):
        runmark.mark_complete(self.run_dir, run_id="r1", inputs="aaaa")
        self.assertTrue(runmark.is_complete(self.run_dir, inputs="aaaa"))
        self.assertFalse(runmark.is_complete(self.run_dir, inputs="bbbb"))
        self.assertEqual(runmark.why_not(self.run_dir, inputs="aaaa"), "reusable")
        self.assertIn("task inputs aaaa != bbbb", runmark.why_not(self.run_dir, inputs="bbbb"))

    def test_marker_without_inputs_stays_reusable(self):
        """Markers written before input identity existed must not invalidate on upgrade."""
        runmark.mark_complete(self.run_dir, run_id="r1")
        marker = json.loads((self.run_dir / runmark.MARK).read_text())
        self.assertNotIn("inputs", marker)
        self.assertTrue(runmark.is_complete(self.run_dir, inputs="aaaa"))
        self.assertEqual(runmark.why_not(self.run_dir, inputs="aaaa"), "reusable")

    def test_caller_without_inputs_keeps_previous_behaviour(self):
        runmark.mark_complete(self.run_dir, run_id="r1", inputs="aaaa")
        self.assertTrue(runmark.is_complete(self.run_dir))
        self.assertEqual(runmark.why_not(self.run_dir), "reusable")


class SummaryInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = self.root / "documents"
        self.files.mkdir()
        (self.files / "ledger.csv").write_text("amount\n1.00\n")
        self.summary = self.root / "summary.json"
        self.res = schedule.TaskResult(task_id="task-1", domain="d", k=1)

    def _write_summary(self, adapter):
        self.summary.write_text(
            json.dumps(
                {
                    "task_id": "task-1",
                    "domain": "d",
                    "k": 1,
                    "status": "ok",
                    "inputs": schedule._task_inputs(adapter, "task-1"),
                    "modes": {"agg": {"ok": True}},
                    "selections": {"agg": {"ok": True, "chosen_label": "c1"}},
                }
            )
        )

    def _resume(self, adapter):
        self.res.inputs = schedule._task_inputs(adapter, "task-1")
        out = io.StringIO()
        with redirect_stdout(out):
            resumed = schedule._resume(
                self.summary,
                "task-1",
                1,
                adapter,
                self.root,
                ("agg",),
                None,
                "",
                {},
                self.res,
            )
        return resumed, out.getvalue()

    def test_changed_source_files_are_not_resumed(self):
        adapter = _FakeAdapter("Sum ledger.csv.", self.files)
        self._write_summary(adapter)
        (self.files / "ledger.csv").write_text("amount\n1.00\n2.00\n")
        resumed, printed = self._resume(adapter)
        self.assertFalse(resumed)
        self.assertIn("changed since this summary was written", printed)

    def test_changed_prompt_is_not_resumed(self):
        adapter = _FakeAdapter("Sum ledger.csv.", self.files)
        self._write_summary(adapter)
        resumed, printed = self._resume(_FakeAdapter("Sum ledger.csv twice.", self.files))
        self.assertFalse(resumed)
        self.assertIn("task inputs", printed)

    def test_summary_without_inputs_is_not_invalidated(self):
        """A summary written before input identity existed keeps its previous meaning."""
        adapter = _FakeAdapter("Sum ledger.csv.", self.files)
        self.summary.write_text(json.dumps({"status": "ok"}))
        resumed, printed = self._resume(adapter)
        self.assertFalse(resumed)
        self.assertNotIn("task inputs", printed)

    def test_write_summary_drops_modes_from_an_older_input_identity(self):
        """Modes recorded under a previous input digest must not survive a re-run.

        `_write_summary` merges per-mode records across processes, but a mode computed
        from the old inputs is an answer to a different question. Carrying it over under
        the new digest would let a later run skip recomputation for a stale result.
        """
        adapter = _FakeAdapter("Sum ledger.csv.", self.files)
        self.summary.write_text(
            json.dumps(
                {
                    "task_id": "task-1",
                    "domain": "d",
                    "k": 1,
                    "status": "ok",
                    "inputs": schedule._task_inputs(adapter, "task-1"),
                    "modes": {"agg": {"ok": True}, "seq": {"ok": True}},
                    "selections": {
                        "agg": {"ok": True, "chosen_label": "c1"},
                        "seq": {"ok": True, "chosen_label": "c1"},
                    },
                }
            )
        )

        # The task changes; only `agg` is re-run and written back.
        (self.files / "ledger.csv").write_text("amount\n1.00\n2.00\n")
        res = schedule.TaskResult(task_id="task-1", domain="d", k=1)
        res.inputs = schedule._task_inputs(adapter, "task-1")
        res.status = "ok"
        res.modes["agg"] = {"ok": True}
        res.selections["agg"] = {"ok": True, "chosen_label": "c2"}
        schedule._write_summary(self.summary, res)

        merged = json.loads(self.summary.read_text())
        self.assertEqual(merged["inputs"], res.inputs)
        self.assertIn("agg", merged["modes"])
        self.assertNotIn("seq", merged["modes"])
        self.assertNotIn("seq", merged["selections"])


class RolloutReuseTests(unittest.TestCase):
    """The reported symptom, end to end through `LabAdapter.execute`.

    The executor is replaced by a deterministic function that writes the current document into
    the run directory: no model call, no downloaded benchmark, no shell.
    """

    task_id = "example/task-1"
    run_id = "lab-cx-agg1/example__task-1/base1"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lab = self.root / "lab"
        self.task = self.lab / "tasks" / self.task_id
        (self.task / "documents").mkdir(parents=True)
        self.task_json = self.task / "task.json"
        self.document = self.task / "documents" / "source.txt"
        self.task_json.write_text(json.dumps({"instructions": "Use version one."}))
        self.document.write_text("version-one")

    def _adapter(self):
        runs = patch.object(config, "RUNS", self.root / "runs")
        lab_repo = patch.object(config, "lab_repo", return_value=self.lab)
        runs.start()
        lab_repo.start()
        self.addCleanup(runs.stop)
        self.addCleanup(lab_repo.stop)
        return LabAdapter(substrate="cx")

    def _runner(self, adapter):
        def runner(task_id, install, run_ids, timeout):
            out = adapter._run_dir(run_ids[0])
            (out / "output").mkdir(parents=True, exist_ok=True)
            (out / "output" / "answer.txt").write_text(self.document.read_text())
            (out / "metrics.json").write_text("{}")
            return [out]

        return runner

    def _answer(self, run_dir):
        return (Path(run_dir) / "output" / "answer.txt").read_text()

    def test_unchanged_inputs_reuse_the_rollout(self):
        adapter = self._adapter()
        with patch.object(adapter.substrate, "execute", side_effect=self._runner(adapter)) as execs:
            first = adapter.execute(self.task_id, None, self.run_id)
            second = adapter.execute(self.task_id, None, self.run_id)
            self.assertEqual(execs.call_count, 1)
            self.assertEqual(self._answer(first), "version-one")
            self.assertEqual(Path(second), Path(first))

    def test_changed_source_files_re_roll_the_rollout(self):
        adapter = self._adapter()
        with patch.object(adapter.substrate, "execute", side_effect=self._runner(adapter)) as execs:
            adapter.execute(self.task_id, None, self.run_id)
            self.document.write_text("version-two")
            out = adapter.execute(self.task_id, None, self.run_id)
            self.assertEqual(execs.call_count, 2)
            self.assertEqual(self._answer(out), "version-two")

    def test_changed_instructions_re_roll_the_rollout(self):
        adapter = self._adapter()
        with patch.object(adapter.substrate, "execute", side_effect=self._runner(adapter)) as execs:
            adapter.execute(self.task_id, None, self.run_id)
            self.task_json.write_text(json.dumps({"instructions": "Use version two."}))
            out = adapter.execute(self.task_id, None, self.run_id)
            self.assertEqual(execs.call_count, 2)
            self.assertIn("version two", adapter.task_prompt(self.task_id))
            # The source material is unchanged, so the fresh answer still matches it.
            self.assertEqual(self._answer(out), "version-one")


if __name__ == "__main__":
    unittest.main()
