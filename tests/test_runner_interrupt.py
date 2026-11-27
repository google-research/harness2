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

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from harness2.benchmarks import jobbench, lab

_DRIVER = """
import importlib, subprocess, sys
from pathlib import Path
from unittest.mock import patch

module = importlib.import_module(sys.argv[1])
root = Path(sys.argv[2])
runner = '''
import os, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
(root / 'ready.tmp').write_text(f'{os.getpid()} {child.pid}')
(root / 'ready.tmp').replace(root / 'ready')
child.wait()
'''
original = subprocess.Popen
started = []
def launch(*args, **kwargs):
    proc = original(*args, **kwargs)
    started.append(proc)
    return proc
with patch.object(module.subprocess, 'Popen', side_effect=launch):
    try:
        module.run_detached([sys.executable, '-c', runner, str(root)], timeout=30,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except KeyboardInterrupt:
        sys.exit(0 if started[0].poll() is not None else 19)
sys.exit(20)
"""


def _running(pid):
    try:
        os.kill(pid, 0)
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except (ProcessLookupError, FileNotFoundError):
        return False


@unittest.skipUnless(sys.platform == "linux", "process-state assertions use Linux /proc")
class RunnerInterruptTests(unittest.TestCase):
    def test_interrupt_stops_runner_and_descendant_before_propagating(self):
        for module in (lab, jobbench):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as td:
                ready = Path(td) / "ready"
                runner_pid = None
                driver = subprocess.Popen(
                    [sys.executable, "-c", _DRIVER, module.__name__, td],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    deadline = time.monotonic() + 10
                    while not ready.exists() and time.monotonic() < deadline:
                        self.assertIsNone(driver.poll(), "driver exited before starting its runner")
                        time.sleep(0.01)
                    self.assertTrue(ready.exists(), "runner did not become ready")
                    runner_pid, child_pid = map(int, ready.read_text().split())
                    os.kill(driver.pid, signal.SIGINT)
                    stdout, stderr = driver.communicate(timeout=10)
                    self.assertEqual(driver.returncode, 0, stdout + stderr)
                    deadline = time.monotonic() + 2
                    while _running(child_pid) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(_running(child_pid), "descendant survived the interrupt")
                    self.assertFalse(_running(runner_pid), "runner survived the interrupt")
                finally:
                    if runner_pid is not None:
                        try:
                            os.killpg(runner_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if driver.poll() is None:
                        driver.kill()
                    driver.communicate()

    def test_normal_exit_nonzero_exit_and_timeout(self):
        for module in (lab, jobbench):
            with self.subTest(module=module.__name__):
                module.run_detached([sys.executable, "-c", "pass"], timeout=5)
                with self.assertRaisesRegex(RuntimeError, "status 7"):
                    module.run_detached([sys.executable, "-c", "raise SystemExit(7)"], timeout=5)
                with self.assertRaises(subprocess.TimeoutExpired):
                    module.run_detached(
                        [sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.1
                    )
