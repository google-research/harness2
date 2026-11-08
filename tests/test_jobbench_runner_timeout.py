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

"""The JobBench runner enforces its own time limit, without the GNU timeout command."""

import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks import jb_opencode_runner as runner


class TimeoutDurationTests(unittest.TestCase):
    def test_plain_seconds_and_suffixes_are_understood(self):
        self.assertEqual(runner._timeout_seconds("3600"), 3600)
        self.assertEqual(runner._timeout_seconds("90s"), 90)
        self.assertEqual(runner._timeout_seconds("30m"), 1800)
        self.assertEqual(runner._timeout_seconds("2h"), 7200)

    def test_a_limit_below_the_floor_or_unparseable_keeps_the_floor(self):
        self.assertEqual(runner._timeout_seconds("5"), 30)
        self.assertEqual(runner._timeout_seconds(""), 30)
        self.assertEqual(runner._timeout_seconds("forever"), 30)


class CommandTests(unittest.TestCase):
    def test_the_command_does_not_shell_out_to_timeout(self):
        cfg = runner.Settings(
            task_ids=("t1",),
            label="base1",
            render_dir=Path("render"),
            tasks_base=Path("tasks"),
            opencode_dir=Path("oc"),
            model_id="fake/model",
            agent_name="",
            variant="",
            agent_steps="",
            timeout_per_task="60",
            max_retries=1,
            log_dir=Path("logs"),
        )
        cmd = runner._command(cfg, Path("ws"), "prompt", "title")

        self.assertNotIn("timeout", cmd)
        self.assertEqual(cmd[0], "bun")


@unittest.skipUnless(os.name == "posix", "the runner stops a POSIX process group")
class TerminateProcessTests(unittest.TestCase):
    def _start(self, handler: str) -> subprocess.Popen:
        script = (
            "import signal, sys, time\n"
            f"signal.signal(signal.SIGTERM, {handler})\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.addCleanup(self._cleanup, proc)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        return proc

    @staticmethod
    def _cleanup(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
        proc.stdout.close()
        proc.stderr.close()

    def test_a_graceful_shutdown_reports_the_time_limit_as_124(self):
        proc = self._start("signal.SIG_DFL")

        self.assertEqual(runner._terminate_process(proc, grace=10), 124)

    def test_ignoring_sigterm_escalates_to_sigkill_and_reports_137(self):
        proc = self._start("signal.SIG_IGN")

        self.assertEqual(runner._terminate_process(proc, grace=1), 137)

    def test_a_child_that_outlives_its_parent_is_killed_and_reported_as_137(self):
        """A descendant ignoring SIGTERM must not survive the parent's exit."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        flag = Path(temporary.name) / "child-ready"
        child_source = (
            "import pathlib,signal,sys,time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "pathlib.Path(sys.argv[1]).write_text('ready')\n"
            "time.sleep(120)\n"
        )
        parent_source = (
            "import pathlib,subprocess,sys,time\n"
            f"c = subprocess.Popen([sys.executable,'-u','-c',{child_source!r}, sys.argv[1]])\n"
            # Only exit once the child's SIGTERM handler is installed, so the test
            # measures the orphan case and not a startup race.
            "deadline = time.monotonic() + 30\n"
            "while not pathlib.Path(sys.argv[1]).exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.02)\n"
            "print(c.pid, flush=True)\n"
            "sys.exit(0)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", parent_source, str(flag)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.addCleanup(self._cleanup, proc)
        child_pid = int(proc.stdout.readline().strip())
        self.addCleanup(self._kill, child_pid)
        self.assertTrue(flag.is_file(), "the child never reported readiness")

        # The parent is already gone, so a plain `proc.wait()` returns at once.
        self.assertEqual(runner._terminate_process(proc, grace=2), 137)
        self.assertFalse(self._alive(child_pid), "the orphaned child survived the time limit")

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _kill(cls, pid: int) -> None:
        if cls._alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_an_already_dead_process_still_reports_the_time_limit(self):
        proc = self._start("signal.SIG_DFL")
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)

        self.assertEqual(runner._terminate_process(proc, grace=5), 124)


if __name__ == "__main__":
    unittest.main()
