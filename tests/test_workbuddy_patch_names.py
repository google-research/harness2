# SPDX-License-Identifier: Apache-2.0

"""Use real Git patches and its NUL-delimited name list as an independent oracle."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.workbuddy import WbAdapter


@unittest.skipUnless(shutil.which("git"), "Git produces the benchmark's patch artifact")
class WorkbuddyPatchNamesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.trial = self.root / "trial"
        (self.trial / "verifier").mkdir(parents=True)
        self.env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "--template=", "-q")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.source, env=self.env, capture_output=True, check=True
        ).stdout

    def write(self, name, body=b"Delivered result\n"):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def patch(self):
        self.git("add", "--all")
        patch = self.git("diff", "--cached", "--binary", "--find-renames")
        names = self.git("diff", "--cached", "--name-only", "-z", "--find-renames")
        (self.trial / "verifier" / "agent.patch").write_bytes(patch)
        return sorted(names.decode("utf-8").split("\0")[:-1])

    def test_listing_matches_git_for_quoted_and_embedded_prefix_names(self):
        for name in (
            "café.txt",
            "普通.txt",
            'a "quote".txt',
            "tab\tname.txt",
            "dir b/nested.txt",
            "trailing ",
            "back\\slash.txt",
            "line\nname.txt",
        ):
            self.write(name)
        expected = self.patch()
        for subset in ("code", "office"):
            with self.subTest(subset=subset):
                self.assertEqual(WbAdapter(subset).deliverable_listing(self.trial), expected)

    def test_rendered_diff_file_index_uses_the_same_decoded_names(self):
        # quotePath=false still quotes literal quotation marks; raw Unicode must
        # remain intact rather than being decoded as a Latin-1 byte escape.
        self.git("config", "core.quotePath", "false")
        for name in (
            "普通.txt",
            "Ã©.txt",
            'a "quote".txt',
            "dir b/nested.txt",
            "trailing ",
            "back\\slash.txt",
            "line\nname.txt",
        ):
            self.write(name)
        expected = self.patch()
        adapter = WbAdapter("code")
        self.assertEqual(adapter.deliverable_listing(self.trial), expected)
        text = adapter.deliverable_text(self.trial)
        for name in expected:
            with self.subTest(name=name):
                self.assertIn(f"- {name}\n", text)

    def test_existing_ascii_binary_delete_and_rename_controls(self):
        self.write("old file.txt")
        self.write("removed.txt")
        self.git("add", "--all")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "base",
        )
        (self.source / "old file.txt").rename(self.source / "new file.txt")
        (self.source / "removed.txt").unlink()
        self.write("space name.txt")
        self.write("binary.bin", b"\x00\x01\x02")
        expected = self.patch()
        self.assertEqual(WbAdapter("code").deliverable_listing(self.trial), expected)
