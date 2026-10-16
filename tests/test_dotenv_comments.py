# SPDX-License-Identifier: Apache-2.0

"""Load real .env files through setup's credential-free dry-run path."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DotenvCommentsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in (
            "harness2/__init__.py",
            "harness2/config.py",
            "harness2/install.py",
            "harness2/cli/__init__.py",
            "harness2/cli/setup.py",
            "pyproject.toml",
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def run_python(self, args, **overrides):
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"}
        }
        env.update(overrides)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def setup_plan(self):
        return self.run_python(
            ["-m", "harness2.cli.setup", "lab", "--substrate", "cx", "--dry-run"]
        )

    def test_commented_quoted_empty_endpoint_is_not_configured(self):
        for quote in ("'", '"'):
            with self.subTest(quote=quote):
                (self.root / ".env").write_text(
                    f"HARNESS2_CODEX_BASE_URL={quote}{quote} # configure later\n", encoding="utf-8"
                )
                result = self.setup_plan()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("HARNESS2_CODEX_BASE_URL is required", result.stderr)
                self.assertFalse((self.root / "dependencies").exists())

    def test_trailing_comment_is_removed_and_quoted_hash_is_preserved(self):
        for quote in ("'", '"'):
            with self.subTest(quote=quote):
                (self.root / ".env").write_text(
                    f"HARNESS2_CODEX_BASE_URL={quote}http://127.0.0.1:1/v1{quote} # local\n"
                    f"HARNESS2_RUNS_DIR={quote}runs # local{quote} # output directory\n",
                    encoding="utf-8",
                )
                result = self.run_python(
                    [
                        "-c",
                        "import json; from harness2 import config as c; "
                        "print(json.dumps([c.CODEX_BASE_URL, str(c.RUNS)]))",
                    ]
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(result.stdout),
                    [
                        "http://127.0.0.1:1/v1",
                        str((self.root / "runs # local").resolve()),
                    ],
                )

    def test_existing_empty_quotes_and_shell_precedence_are_unchanged(self):
        (self.root / ".env").write_text(
            'HARNESS2_CODEX_BASE_URL=""\nGOOGLE_CLOUD_LOCATION="from-file"\n', encoding="utf-8"
        )
        result = self.setup_plan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("HARNESS2_CODEX_BASE_URL is required", result.stderr)
        result = self.run_python(
            ["-c", "from harness2 import config; print(config.VERTEX_REGION)"],
            GOOGLE_CLOUD_LOCATION="from-shell",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "from-shell")
