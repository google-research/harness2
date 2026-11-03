"""Opt-in native Codex sandbox checks. These run local commands, not models.

HARNESS2_TEST_NATIVE_SANDBOX=1 python -m unittest tests.test_subscription_sandbox
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import subscription


@unittest.skipUnless(
    os.environ.get("HARNESS2_TEST_NATIVE_SANDBOX") == "1"
    and sys.platform == "darwin"
    and shutil.which("codex"),
    "Opt-in macOS native Codex sandbox check",
)
class NativeSandboxTests(unittest.TestCase):
    expected = {
        "own": "readable",
        "other": "blocked",
        "shared": "blocked",
        "repository": "blocked",
        "private_tmp": "writable",
    }

    def _check(self, edit: bool, remove_boundary: bool = False):
        with (
            tempfile.TemporaryDirectory(prefix="harness2-inside-") as td,
            tempfile.TemporaryDirectory(prefix="harness2-outside-") as other,
            tempfile.TemporaryDirectory(prefix="harness2-shared-", dir="/tmp") as shared,
            tempfile.TemporaryDirectory(prefix=".harness2-test-", dir=Path.cwd()) as repo,
        ):
            workspace = Path(td)
            files = {}
            for name, directory in (
                ("own", td),
                ("other", other),
                ("shared", shared),
                ("repository", repo),
            ):
                target = Path(directory) / "canary.txt"
                target.write_text("synthetic boundary canary")
                files[name] = str(target.resolve())

            captured = {}

            def capture(cmd, **kwargs):
                captured.update(cmd=cmd, env=kwargs["env"])
                kwargs["stdout"].write('{"type":"turn.completed"}\n')
                return Mock(returncode=0)

            with patch.object(subscription.subprocess, "Popen", side_effect=capture):
                subscription.invoke(
                    "codex_cli",
                    "unused",
                    "unused",
                    workspace=workspace,
                    audit=workspace / "audit",
                    edit=edit,
                )

            # Exercise the permission flags actually passed by invoke(), without
            # a model call or access to the native login credentials.
            configs = []
            for i, arg in enumerate(captured["cmd"]):
                if arg == "-c" and captured["cmd"][i + 1].startswith("permissions."):
                    value = captured["cmd"][i + 1]
                    if remove_boundary and ".filesystem=" in value:
                        continue
                    configs += ["-c", value]
            script = (
                "import json,os; from pathlib import Path\n"
                f"paths={files!r}\n"
                "result={}\n"
                "for name,path in paths.items():\n"
                " try: Path(path).read_bytes(); result[name]='readable'\n"
                " except PermissionError: result[name]='blocked'\n"
                "Path(os.environ['TMPDIR'],'scratch').write_text('ok')\n"
                "result['private_tmp']='writable'\n"
                "print(json.dumps(result))\n"
            )
            completed = subprocess.run(
                [
                    "codex",
                    "sandbox",
                    "-C",
                    td,
                    "-P",
                    "harness2",
                    *configs,
                    "--",
                    "/usr/bin/python3",
                    "-c",
                    script,
                ],
                env=captured["env"],
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout)

    def test_executor_boundary(self):
        self.assertEqual(self._check(edit=True), self.expected)

    def test_judge_boundary(self):
        self.assertEqual(self._check(edit=False), self.expected)

    def test_canary_rejects_missing_boundary(self):
        actual = self._check(edit=True, remove_boundary=True)
        self.assertEqual(
            actual,
            {**self.expected, "other": "readable", "shared": "readable", "repository": "readable"},
        )
        self.assertNotEqual(actual, self.expected)


if __name__ == "__main__":
    unittest.main()
