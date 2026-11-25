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

"""Exercise the CLI's judged.json read/merge/write with a local score adapter."""

import fcntl
import json
import multiprocessing
import os
import stat
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness2.cli import judge
from harness2.core import judge as core_judge


class LocalScores:
    substrate = SimpleNamespace(name="oc")

    def __init__(self, root):
        self.spec = SimpleNamespace(name="lab", runs_root=root)

    @staticmethod
    def score(run_dir, task_id):
        return json.loads((run_dir / "score.json").read_text(encoding="utf-8"))


def run_mode(root, mode, first_entered, second_entered, first_published):
    write = core_judge.write
    flock = fcntl.flock

    def observe_lock(fd, operation):
        if mode == "sequential" and operation == fcntl.LOCK_EX:
            second_entered.set()
        return flock(fd, operation)

    def ordered_write(rows, path):
        if mode == "parallel":
            first_entered.set()
            if not second_entered.wait(5):
                raise TimeoutError("second mode did not reach publication")
            ret = write(rows, path)
            first_published.set()
            return ret
        second_entered.set()
        if not first_published.wait(5):
            raise TimeoutError("first mode did not write")
        return write(rows, path)

    with (
        patch.object(judge.config, "validate_models"),
        patch.object(judge.C, "make_adapter", return_value=LocalScores(root)),
        patch.object(fcntl, "flock", side_effect=observe_lock),
        patch.object(core_judge, "write", side_effect=ordered_write),
    ):
        raise SystemExit(judge.main(["--bench", "lab", "--k", "1", "--mode", mode]))


def run_paused_judge(root, scored, release):
    judge_selections = core_judge.judge_selections

    def pause_after_scoring(*args, **kwargs):
        rows = judge_selections(*args, **kwargs)
        scored.set()
        if not release.wait(5):
            raise TimeoutError("older judge was not released")
        return rows

    with (
        patch.object(judge.config, "validate_models"),
        patch.object(judge.C, "make_adapter", return_value=LocalScores(root)),
        patch.object(core_judge, "judge_selections", side_effect=pause_after_scoring),
    ):
        raise SystemExit(judge.main(["--bench", "lab", "--k", "1", "--mode", "parallel"]))


def selection(root, mode, points):
    run_dir = root / f"rollout-{mode}"
    run_dir.mkdir()
    (run_dir / "score.json").write_text(
        json.dumps({"n_passed": points, "n_criteria": 2}), encoding="utf-8"
    )
    return {
        "task_id": "one",
        "mode": mode,
        "k": 1,
        "ok": True,
        "chosen_label": f"BASE-{mode}",
        "chosen_run_id": f"one/{mode}/BASE",
        "chosen_run_dir": str(run_dir),
        "selected_at": "2026-10-31T01:00:00Z",
    }


class JudgePublication(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        ns_dir = self.root / "adapt" / "lab-oc-agg1"
        self.summary = ns_dir / "one" / "summary.json"
        self.summary.parent.mkdir(parents=True)
        self.summary.write_text(
            json.dumps(
                {
                    "task_id": "one",
                    "selections": {
                        "agg": selection(self.root, "agg", 1),
                        "seq": selection(self.root, "seq", 2),
                    },
                }
            ),
            encoding="utf-8",
        )
        self.judged = ns_dir / "judged.json"

    def test_overlapping_per_mode_judges_keep_both_rows(self):
        ctx = multiprocessing.get_context("fork")
        signals = [ctx.Event(), ctx.Event(), ctx.Event()]
        first = ctx.Process(target=run_mode, args=(self.root, "parallel", *signals))
        second = ctx.Process(target=run_mode, args=(self.root, "sequential", *signals))
        first.start()
        try:
            self.assertTrue(signals[0].wait(5), "parallel mode never reached its write")
            second.start()
            first.join(10)
            second.join(10)
            self.assertEqual((first.exitcode, second.exitcode), (0, 0))
            rows = json.loads(self.judged.read_text(encoding="utf-8"))
            self.assertEqual({row["mode"]: row["n_passed"] for row in rows}, {"agg": 1, "seq": 2})
        finally:
            for process in (first, second):
                if process.is_alive():
                    process.terminate()
                    process.join(5)

    def test_serial_per_mode_judges_keep_both_rows(self):
        with (
            patch.object(judge.config, "validate_models"),
            patch.object(judge.C, "make_adapter", return_value=LocalScores(self.root)),
        ):
            for mode in ("parallel", "sequential"):
                self.assertEqual(judge.main(["--bench", "lab", "--k", "1", "--mode", mode]), 0)
        rows = json.loads(self.judged.read_text(encoding="utf-8"))
        self.assertEqual({row["mode"]: row["n_passed"] for row in rows}, {"agg": 1, "seq": 2})

    def test_late_judge_cannot_replace_a_grade_for_the_current_selection(self):
        ctx = multiprocessing.get_context("fork")
        scored, release = ctx.Event(), ctx.Event()
        older = ctx.Process(target=run_paused_judge, args=(self.root, scored, release))
        older.start()
        try:
            self.assertTrue(scored.wait(5), "older judge did not finish scoring")
            summary = json.loads(self.summary.read_text(encoding="utf-8"))
            newer = summary["selections"]["agg"]
            # The selection stamp identifies the generation even if the system clock moved back.
            newer["selected_at"] = "2026-10-31T00:59:00Z"
            rollout = Path(newer["chosen_run_dir"])
            (rollout / "score.json").write_text(
                json.dumps({"n_passed": 0, "n_criteria": 2}), encoding="utf-8"
            )
            staged = self.summary.with_suffix(".tmp")
            staged.write_text(json.dumps(summary), encoding="utf-8")
            staged.replace(self.summary)
            with (
                patch.object(judge.config, "validate_models"),
                patch.object(judge.C, "make_adapter", return_value=LocalScores(self.root)),
            ):
                self.assertEqual(
                    judge.main(["--bench", "lab", "--k", "1", "--mode", "parallel"]), 0
                )

            published = self.judged.read_bytes()
            (row,) = json.loads(published)
            self.assertEqual(row["run_id"], newer["chosen_run_id"])
            self.assertEqual(row["selected_at"], newer["selected_at"])
            self.assertEqual(row["n_passed"], 0)
            release.set()
            older.join(10)
            self.assertEqual(self.judged.read_bytes(), published)
            self.assertEqual(older.exitcode, 2)
        finally:
            release.set()
            if older.is_alive():
                older.terminate()
                older.join(5)

    def test_current_failed_selection_replaces_a_grade(self):
        args = ["--bench", "lab", "--k", "1", "--mode", "parallel"]
        with (
            patch.object(judge.config, "validate_models"),
            patch.object(judge.C, "make_adapter", return_value=LocalScores(self.root)),
        ):
            self.assertEqual(judge.main(args), 0)
            summary = json.loads(self.summary.read_text(encoding="utf-8"))
            summary["selections"]["agg"] = {"task_id": "one", "arm": "agg", "k": 1, "ok": False}
            self.summary.write_text(json.dumps(summary), encoding="utf-8")
            self.assertEqual(judge.main(args), 2)

        (row,) = json.loads(self.judged.read_bytes())
        self.assertEqual(row["mode"], "agg")
        self.assertFalse(row["graded"])
        self.assertTrue(row["error"].startswith("selection did not complete"))

    def test_failed_publication_keeps_cache_and_releases_lock(self):
        args = ["--bench", "lab", "--k", "1", "--mode"]
        lock_path = self.judged.with_suffix(".lock")
        with (
            patch.object(judge.config, "validate_models"),
            patch.object(judge.C, "make_adapter", return_value=LocalScores(self.root)),
        ):
            self.assertEqual(judge.main([*args, "parallel"]), 0)
            old = self.judged.read_bytes()
            old_lock_inode = lock_path.stat().st_ino
            visible_during_write = []
            fdopen = os.fdopen

            @contextmanager
            def open_failing_stream(*args, **kwargs):
                with fdopen(*args, **kwargs) as stream:

                    def fail_after_prefix(data):
                        stream.write(data[:5])
                        stream.flush()
                        visible_during_write.append(self.judged.read_bytes())
                        raise OSError("simulated interrupted file write")

                    failing_stream = Mock(wraps=stream)
                    failing_stream.write.side_effect = fail_after_prefix
                    yield failing_stream

            with (
                patch.object(core_judge.os, "fdopen", open_failing_stream),
                self.assertRaisesRegex(OSError, "interrupted file write"),
            ):
                judge.main([*args, "sequential"])

            self.assertEqual(visible_during_write, [old])
            self.assertEqual(self.judged.read_bytes(), old)
            self.assertEqual(list(self.judged.parent.glob("*.tmp")), [])
            with lock_path.open() as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(judge.main([*args, "sequential"]), 0)

        self.assertEqual(lock_path.stat().st_ino, old_lock_inode)
        rows = json.loads(self.judged.read_text(encoding="utf-8"))
        self.assertEqual({row["mode"]: row["n_passed"] for row in rows}, {"agg": 1, "seq": 2})

    def test_atomic_write_keeps_output_permissions(self):
        row = core_judge.Scored(
            "one", "agg", 1, "BASE", "one/agg/BASE", True, n_passed=1, n_criteria=2
        )
        control = self.root / "ordinary-output"
        control.touch()
        core_judge.write([row], self.judged)
        self.assertEqual(
            stat.S_IMODE(self.judged.stat().st_mode), stat.S_IMODE(control.stat().st_mode)
        )
        self.judged.chmod(0o640)
        core_judge.write([row], self.judged)
        self.assertEqual(stat.S_IMODE(self.judged.stat().st_mode), 0o640)


if __name__ == "__main__":
    unittest.main()
