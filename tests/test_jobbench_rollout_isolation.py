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

"""JobBench OpenCode rollouts must not share install, temp, or workspace state."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks import jb_opencode_runner as runner

FAKE_BUN = """#!/usr/bin/env python3
import json, os, sys, tempfile
from pathlib import Path

ws = Path(sys.argv[sys.argv.index("--dir") + 1])
home = Path(os.environ["HOME"])
npm = Path(os.environ.get("NPM_CONFIG_PREFIX") or home / ".npm-global")
user = Path(os.environ.get("PYTHONUSERBASE") or home / ".local")
leaks = {
    "npm_global": npm / "lib" / "node_modules" / "leaked-pkg" / "index.js",
    "pip_user": user / "lib" / "leaked_mod.py",
    "tmpdir": Path(tempfile.gettempdir()) / "leaked-scratch.txt",
}
first_attempt = sys.argv[sys.argv.index("--title") + 1].endswith("| a1")
mode = os.environ["FAKE_AGENT"]
if mode == "install" or (mode == "flaky" and first_attempt):
    for path in leaks.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("left by an earlier rollout")
if mode == "flaky" and first_attempt:
    sys.exit(1)
report = {
    "workspace": str(ws),
    "sees": {name: path.is_file() for name, path in leaks.items()},
    "env": {k: os.environ.get(k, "") for k in
            ("TMPDIR", "NPM_CONFIG_PREFIX", "PYTHONUSERBASE", "PATH", "OPENCODE_PERMISSION")},
}
(ws / "output" / "report.json").write_text(json.dumps(report))
print('{"type":"step_finish"}')
"""


def _opencode_allows(permission: str, path: Path) -> bool:
    """OpenCode v1.14.18's external_directory verdict for a file: last matching rule wins."""
    request = f"{path.parent}/*"
    verdict = None
    for pattern, action in json.loads(permission)["external_directory"].items():
        rx = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
        if re.match(f"^{rx}$", request, re.S):
            verdict = action
    return verdict == "allow"


class RolloutIsolationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "bun").write_text(FAKE_BUN)
        (bin_dir / "bun").chmod(0o755)
        (self.root / "home").mkdir()
        (self.root / "host-tmp").mkdir()
        self.tasks = self.root / "tasks"
        (self.tasks / "t1" / "task_folder").mkdir(parents=True)
        (self.tasks / "t1" / "task_folder" / "TASK_INSTRUCTIONS.txt").write_text("do it")
        (self.tasks / ".logs").mkdir()
        (self.root / "render" / ".opencode").mkdir(parents=True)
        (self.root / "oc" / "packages" / "opencode").mkdir(parents=True)
        self.temp_base = self.root / "hevo_ws"
        self.temp_base.mkdir()
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "TMPDIR": str(self.root / "host-tmp"),
        }
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("NPM_CONFIG_PREFIX", "PYTHONUSERBASE"):
            os.environ.pop(key, None)
        base_patch = patch.object(runner, "TEMP_BASE", self.temp_base)
        base_patch.start()
        self.addCleanup(base_patch.stop)

    def _roll(self, label: str, agent: str, retries: int = 1) -> dict:
        cfg = runner.Settings(
            task_ids=("t1",),
            label=label,
            render_dir=self.root / "render",
            tasks_base=self.tasks,
            opencode_dir=self.root / "oc",
            model_id="fake/model",
            agent_name="",
            variant="",
            agent_steps="",
            timeout_per_task="60",
            max_retries=retries,
            log_dir=self.tasks / ".logs",
        )
        with patch.dict(os.environ, {"FAKE_AGENT": agent}):
            self.assertTrue(runner.run_one(cfg, "t1"))
        out = self.tasks / "t1" / "model_output" / label / "report.json"
        return json.loads(out.read_text())

    def test_later_rollout_cannot_see_earlier_installs_or_temp_files(self):
        first = self._roll("base1", "install")
        second = self._roll("inv1", "probe")

        self.assertEqual(second["sees"], {"npm_global": False, "pip_user": False, "tmpdir": False})
        self.assertNotEqual(first["env"]["TMPDIR"], second["env"]["TMPDIR"])
        self.assertNotEqual(second["env"]["TMPDIR"], str(self.root / "host-tmp"))
        self.assertEqual(list(self.temp_base.iterdir()), [])

    def test_retry_starts_from_a_clean_sandbox(self):
        report = self._roll("base1", "flaky", retries=2)

        self.assertEqual(report["sees"], {"npm_global": False, "pip_user": False, "tmpdir": False})

    def test_temp_dir_leaves_room_for_unix_socket_paths(self):
        workspace = Path("/tmp/hevo_ws") / f"{'long-task-id' * 12}_base1_4194304"
        tmpdir = runner._isolation_env(workspace, "/usr/bin")["TMPDIR"]

        self.assertLessEqual(len(tmpdir) + len("/pymp-xxxxxxxx/listener-xxxxxxxx"), 107)

    def test_installed_commands_stay_on_path(self):
        report = self._roll("base1", "probe")
        env = report["env"]

        self.assertTrue(env["NPM_CONFIG_PREFIX"])
        self.assertTrue(env["PYTHONUSERBASE"])
        head = env["PATH"].split(":")[:2]
        self.assertEqual(head, [f"{env['NPM_CONFIG_PREFIX']}/bin", f"{env['PYTHONUSERBASE']}/bin"])

    def test_fence_blocks_other_rollouts_but_keeps_own_dirs_and_tmp(self):
        report = self._roll("base1", "probe")
        permission = report["env"]["OPENCODE_PERMISSION"]
        own = Path(report["workspace"])
        sibling = self.temp_base / "t1_dom1_99999" / "output" / "answer.docx"

        self.assertFalse(_opencode_allows(permission, sibling))
        self.assertTrue(_opencode_allows(permission, own / "output" / "answer.docx"))
        self.assertTrue(_opencode_allows(permission, Path(report["env"]["TMPDIR"]) / "x.txt"))
        self.assertTrue(_opencode_allows(permission, Path("/tmp/scratch/notes.txt")))


if __name__ == "__main__":
    unittest.main()
