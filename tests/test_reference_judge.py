# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
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

"""Offline integration of reference execution, completion and judging."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks import lab
from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.cli import judge
from harness2.core import runmark, schedule
from harness2.core.proposer import Proposer, Reply
from harness2.core.spec import resolve_channel_model

_TASK = "accounting/task-1"
_OTHER_TASK = "accounting/task-2"
_GRADE = {"n_passed": 1, "n_criteria": 1, "all_pass": True}


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class _OfflineProposer(Proposer):
    def call(self, mission, context_dir, label, **kwargs):
        return Reply(
            label=label,
            text="<notes>Keep the harness.</notes>",
            ok=True,
            attempts=1,
            workspace=Path(context_dir) / "harness",
        )


class ReferenceJudgeTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for task in (_TASK, _OTHER_TASK):
            task_dir = root / "harvey-labs" / "tasks" / task
            task_dir.mkdir(parents=True)
            _write(
                task_dir / "task.json",
                {
                    "title": "Review a local report",
                    "instructions": "Write a report",
                    "deliverables": {"report.txt": "A short report"},
                },
            )
        self.enterContext(patch.object(config, "RUNS", root / "runs"))
        self.enterContext(patch.object(config, "DEPENDENCIES", root))
        self.enterContext(patch.object(lab, "_opencode_base_body", return_value="Write a report."))
        self.enterContext(patch.object(lab.LabAdapter, "validate_tree", return_value=None))
        self.enterContext(patch.object(lab.LabAdapter, "deliverable_files", self._extract))
        self.enterContext(
            patch.object(lab.LabAdapter, "_launch_oc", lambda a, r, i: self._launcher(a, r, i))
        )
        self.adapter = lab.LabAdapter()
        channel = resolve_channel_model("opencode", "fixture/improver")
        self.proposer = _OfflineProposer(channel)
        self.enterContext(patch.object(lab, "run_detached", side_effect=self._score_command))
        self.runs_root = root / "sweeps"
        self.namespace = schedule.Namespace("lab", "oc", "agg", 1)
        self.namespace_dir = self.runs_root / str(self.namespace)
        self.summary = self.namespace.task_dir(self.runs_root, _TASK) / "summary.json"
        self.rollout_started = Event()
        self.release_rollout = Event()
        self.score_started = Event()
        self.release_score = Event()
        self.hold_rollout = False
        self.hold_score = False
        self.fail_rollout = False
        self.reference_launches = 0
        self.score_reads = []

    @staticmethod
    def _extract(adapter, path):
        return {p.name: p.read_text(encoding="utf-8") for p in (path / "output").glob("*.txt")}

    def _launcher(self, adapter, request, install):
        (run_id,) = request.run_ids
        out = adapter._run_dir(run_id)
        (out / "output").mkdir(parents=True)
        report = out / "output/report.txt"
        is_reference = request.task_ref == _TASK and run_id.endswith("/base1")
        if is_reference:
            self.reference_launches += 1
        if is_reference and self.hold_rollout:
            report.write_text("partial report", encoding="utf-8")
            self.rollout_started.set()
            if not self.release_rollout.wait(10):
                raise RuntimeError("test rollout gate timed out")
        if is_reference and self.fail_rollout:
            return [out]
        report.write_text("complete report", encoding="utf-8")
        _write(out / "metrics.json", {})
        return [out]

    def _score_command(self, command, **kwargs):
        out = Path(command[command.index("--run-id") + 1])
        content = (out / "output/report.txt").read_text(encoding="utf-8")
        self.score_reads.append(content)
        if self.hold_score:
            self.score_started.set()
            if not self.release_score.wait(10):
                raise RuntimeError("test score gate timed out")
        passed = content == "complete report"
        _write(
            out / "scores.json",
            {
                "n_passed": int(passed),
                "n_criteria": 1,
                "all_pass": passed,
            },
        )

    def _run(self, mode, task=_TASK):
        return schedule.run_task(
            task,
            1,
            adapter=self.adapter,
            prop=self.proposer,
            selector_prop=self.proposer,
            runs_root=self.runs_root,
            modes=(mode,),
        )

    def _judge(self):
        status = judge.judge_bases(self.adapter, self.namespace_dir)
        rows = _read(self.namespace_dir / "base_judged.json")
        return status, [row["score"] for row in rows]

    def _invalidate(self):
        saved = _read(self.summary)
        reference = Path(saved["base"][0]["run_dir"])
        (reference / "metrics.json").unlink()
        return reference

    def test_same_path_reroll_is_ungraded_until_its_summary_is_published(self):
        self._check_unpublished_reroll(legacy=False)

    def test_first_reroll_also_guards_a_legacy_summary(self):
        self._check_unpublished_reroll(legacy=True)

    def _check_unpublished_reroll(self, *, legacy):
        self.assertEqual(self._run("agg").status, "ok")
        self.assertEqual(self._judge(), (0, [_GRADE]))
        initial = _read(self.summary)
        self.assertEqual(self._run("seq").status, "ok")
        self.assertEqual(self._judge(), (0, [_GRADE]))
        self.assertEqual(self.reference_launches, 1)

        reference = self._invalidate()
        old_record = _read(self.summary)
        if legacy:
            old_record["base"][0].pop("completion", None)
            _write(self.summary, old_record)
        seq_run = Path(old_record["selections"]["seq"]["chosen_run_dir"])
        (seq_run / "metrics.json").unlink()
        old_summary = self.summary.read_bytes()
        self.hold_rollout = True
        finishing = Event()
        publish = Event()
        original = schedule._write_summary

        def hold_summary(path, result):
            finishing.set()
            if not publish.wait(10):
                raise RuntimeError("test summary gate timed out")
            original(path, result)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(schedule, "_write_summary", side_effect=hold_summary):
                retry = pool.submit(self._run, "seq")
                try:
                    self.assertTrue(self.rollout_started.wait(10))
                    if legacy:
                        guarded = _read(self.summary)
                        self.assertIsNone(guarded["base"][0].pop("completion"))
                        self.assertEqual(guarded, old_record)
                    else:
                        self.assertEqual(self.summary.read_bytes(), old_summary)
                    guarded_summary = self.summary.read_bytes()
                    self.assertIsNone(runmark.read_mark(reference))
                    self.assertEqual(self._judge(), (2, [None]))
                    self.assertFalse((reference / "scores.json").exists())
                    self.release_rollout.set()
                    self.assertTrue(finishing.wait(10))
                    self.assertEqual(self.summary.read_bytes(), guarded_summary)
                    self.assertTrue(self.adapter.artifacts_intact(reference))
                    self.assertEqual(self._judge(), (2, [None]))
                    self.assertFalse((reference / "scores.json").exists())
                finally:
                    self.release_rollout.set()
                    publish.set()
                self.assertEqual(retry.result(timeout=10).status, "ok")
        self.assertEqual(self._judge(), (0, [_GRADE]))
        final = _read(self.summary)
        self.assertEqual(final["selections"]["agg"], initial["selections"]["agg"])
        self.assertNotEqual(final["base"][0]["completion"], initial["base"][0]["completion"])
        self.assertEqual(self.score_reads, ["complete report", "complete report"])

    def test_same_task_reroll_waits_for_score_and_other_task_can_progress(self):
        self.assertEqual(self._run("agg").status, "ok")
        self.hold_score = True
        reference = Path(_read(self.summary)["base"][0]["run_dir"])
        writer_at_lock = Event()
        original = runmark.reference_lock

        def reference_lock(path, *, blocking=True):
            if blocking and path == self.summary:
                writer_at_lock.set()
            return original(path, blocking=blocking)

        with ThreadPoolExecutor(max_workers=3) as pool:
            with patch.object(runmark, "reference_lock", side_effect=reference_lock):
                scoring = pool.submit(judge.judge_bases, self.adapter, self.namespace_dir)
                try:
                    self.assertTrue(self.score_started.wait(10))
                    # Make a different input identity require a new reference, without
                    # changing the artifact that the already-started judge is reading.
                    task = config.lab_repo() / "tasks" / _TASK / "task.json"
                    _write(task, _read(task) | {"instructions": "A changed question"})
                    self.hold_rollout = True
                    reroll = pool.submit(self._run, "seq")
                    self.assertTrue(writer_at_lock.wait(10))
                    other = pool.submit(self._run, "agg", _OTHER_TASK)
                    self.assertEqual(other.result(timeout=10).status, "ok")
                    self.assertFalse(self.rollout_started.is_set())
                    self.assertFalse((reference / "scores.json").exists())
                    self.release_score.set()
                    self.assertEqual(scoring.result(timeout=10), 0)
                    self.assertTrue(self.rollout_started.wait(10))
                    self.assertFalse((reference / "scores.json").exists())
                finally:
                    self.release_score.set()
                    self.release_rollout.set()
                self.assertEqual(reroll.result(timeout=10).status, "ok")
        self.assertEqual(self._judge(), (0, [_GRADE, _GRADE]))

    def test_two_base_judges_preserve_all_successful_grades(self):
        for task in (_TASK, _OTHER_TASK):
            self.assertEqual(self._run("agg", task).status, "ok")
        self.hold_score = True
        second_at_report_lock = Event()
        original = runmark.lock_file

        def lock_file(path, *, blocking=True):
            if path == self.namespace_dir / ".base_judge.lock":
                second_at_report_lock.set()
            return original(path, blocking=blocking)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(judge.judge_bases, self.adapter, self.namespace_dir)
            try:
                self.assertTrue(self.score_started.wait(10))
                with patch.object(runmark, "lock_file", side_effect=lock_file):
                    second = pool.submit(judge.judge_bases, self.adapter, self.namespace_dir)
                    self.assertTrue(second_at_report_lock.wait(10))
            finally:
                self.release_score.set()
            self.assertEqual(first.result(timeout=10), 0)
            self.assertEqual(second.result(timeout=10), 0)
        rows = _read(self.namespace_dir / "base_judged.json")
        self.assertEqual([row["score"] for row in rows], [_GRADE, _GRADE])
        self.assertEqual(len(self.score_reads), 2)

    def test_unmarked_failed_rollout_and_scoring_exception_release(self):
        self.assertEqual(self._run("agg").status, "ok")
        with patch.object(self.adapter, "score", side_effect=RuntimeError("offline judge failure")):
            self.assertEqual(self._judge(), (2, [None]))
        self.assertEqual(self._judge(), (0, [_GRADE]))
        reference = self._invalidate()
        self.fail_rollout = True
        self.assertEqual(self._run("seq").status, "base-failed")
        self.assertFalse((reference / "scores.json").exists())
        self.assertEqual(self._judge(), (2, [None]))

    def test_jobbench_uses_trajectory_marker_and_accepts_a_real_zero(self):
        adapter = JobBenchAdapter(store_root=self.runs_root / "jobbench-store")
        task = "accounting/task1"
        run_id = "jb-oc-agg1/accounting/task1/base1"
        label = run_id.replace("/", "__")
        run = adapter._out_dir(task, label)
        run.mkdir(parents=True)
        (run / "report.txt").write_text("finished answer", encoding="utf-8")
        mark = adapter._traj_dir(task, label)
        runmark.mark_complete(
            mark,
            run_id=run_id,
            inputs="inputs",
            extra={"output_fingerprint": runmark.fingerprint(run)},
        )
        self.assertFalse((run / runmark.MARK).exists())
        completion = runmark.read_mark(mark)
        self.summary.parent.mkdir(parents=True)
        record = {
            "task_id": task,
            "inputs": "inputs",
            "base": [
                {
                    "label": "base1",
                    "run_id": run_id,
                    "run_dir": str(run),
                    "completion": completion,
                }
            ],
        }
        _write(self.summary, record)
        _, details = adapter._details_candidates(task, label)
        details.parent.mkdir(parents=True)
        _write(details, {"total_score": 0, "max_score": 2})
        with patch.object(adapter, "_run_judge", side_effect=AssertionError("external judge")):
            self.assertEqual(judge.judge_bases(adapter, self.namespace_dir), 0)
            (row,) = _read(self.namespace_dir / "base_judged.json")
            self.assertEqual(row["score"]["n_passed"], 0)
            self.assertEqual(row["score"]["n_criteria"], 2)
            record["inputs"] = "different"
            _write(self.summary, record)
            self.assertEqual(judge.judge_bases(adapter, self.namespace_dir), 2)
            (row,) = _read(self.namespace_dir / "base_judged.json")
            self.assertIsNone(row["score"])


if __name__ == "__main__":
    unittest.main()
