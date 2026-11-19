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

"""Replay the documented .env setup without accounts or model calls."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EnvExampleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        package = self.root / "harness2"
        package.mkdir()
        for name in ("__init__.py", "config.py"):
            shutil.copyfile(ROOT / "harness2" / name, package / name)
        shutil.copyfile(ROOT / "pyproject.toml", self.root / "pyproject.toml")

    def configuration(self, **overrides):
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"}
        }
        env.update(overrides)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json; from harness2 import config as c; "
                "print(json.dumps({'project': c.VERTEX_PROJECT, 'region': c.VERTEX_REGION, "
                "'solver': c.SOLVER_MODEL_OC, 'judge': c.JUDGE_MODEL}))",
            ],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @unittest.skipUnless(shutil.which("cp"), "requires the documented POSIX cp command")
    def test_documented_copy_loads_an_unconfigured_example(self):
        example = ROOT / ".env.example"
        if example.is_file():
            shutil.copyfile(example, self.root / ".env.example")
        command = next(
            line
            for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
            if line == "cp .env.example .env"
        )
        result = subprocess.run(shlex.split(command), cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.configuration(),
            {
                "project": "",
                "region": "global",
                "solver": "",
                "judge": "",
            },
        )

    def test_existing_file_loading_preserves_shell_precedence(self):
        (self.root / ".env").write_text("GOOGLE_CLOUD_LOCATION=from-file\n", encoding="utf-8")
        self.assertEqual(
            self.configuration(GOOGLE_CLOUD_LOCATION="from-shell")["region"], "from-shell"
        )
