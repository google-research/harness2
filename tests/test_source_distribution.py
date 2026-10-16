# SPDX-License-Identifier: Apache-2.0
"""Build a real source archive and replay its offline setup/provenance consumers."""

import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    BUILD_BACKEND = int(importlib.metadata.version("setuptools").split(".")[0]) >= 77
except importlib.metadata.PackageNotFoundError:
    BUILD_BACKEND = False


@unittest.skipUnless(BUILD_BACKEND, "Project build requirement setuptools>=77 is needed")
class SourceDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        source = root / "source"
        shutil.copytree(
            ROOT,
            source,
            ignore=shutil.ignore_patterns(
                ".git", "*.egg-info", "build", "dist", "__pycache__", ".ruff_cache", ".env"
            ),
        )
        output = root / "dist"
        cls.env = {
            k: os.environ[k]
            for k in ("HOME", "PATH", "TMPDIR", "LANG", "PYTHONPATH")
            if k in os.environ
        }
        cls.env["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                "from setuptools.build_meta import build_sdist; import sys; build_sdist(sys.argv[1])",
                str(output),
            ],
            cwd=source,
            env=cls.env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if process.returncode:
            raise AssertionError(process.stdout + process.stderr)
        extracted = root / "extracted"
        extracted.mkdir()
        with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
            archive.extractall(extracted, filter="data")
        cls.release = next(extracted.iterdir())

    def test_source_release_has_setup_runners_and_licenses(self):
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                "from harness2.install import missing_runners; import sys; message=missing_runners(); print(message); sys.exit(bool(message))",
            ],
            cwd=self.release,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        for name in ("jb_runner", "lab_runner", "wb_runner"):
            self.assertTrue((self.release / "third_party" / name / "LICENSE").is_file())

    def test_source_release_can_verify_asset_provenance(self):
        process = subprocess.run(
            [sys.executable, "scripts/check_provenance.py"],
            cwd=self.release,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        expected = json.loads((ROOT / "dependencies/ASSETS.json").read_text())
        self.assertIn(f"PASS: {len(expected)} release asset checksums", process.stdout)
