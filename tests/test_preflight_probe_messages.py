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

"""What a readiness probe says when the command it ran cannot run at all.

`preflight.check` shells out to the vendor tooling (git, gcloud, docker, a benchmark's own
interpreter). When the subprocess itself cannot start or never answers, the issue it recorded
named only the exception class -- "Google Application Default Credentials: FileNotFoundError;
see README.md" -- which leaves the deployer to guess which program is missing. The three cases
below are the ones an unconfigured machine actually produces.
"""

import subprocess
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from harness2 import config, preflight


class _Substrate:
    name = "oc"

    def preflight(self, **_kwargs):
        return None


class _Adapter:
    def __init__(self):
        class _Spec:
            name = "lab"

        self.spec = _Spec()
        self.substrate = _Substrate()

    def tasks(self, split="all"):
        return ["t"]

    def preflight(self):
        return None


def _missing_program(argv, **_kwargs):
    error = FileNotFoundError(2, "No such file or directory")
    error.filename = str(argv[0])
    raise error


def _silent(argv, **_kwargs):
    raise subprocess.TimeoutExpired([str(a) for a in argv], 45)


def _missing_cwd(argv, *, cwd=None, **_kwargs):
    """How subprocess reports that the *directory* a probe runs in is not there: the kernel
    names the cwd, not the program. Two callers pass a cwd -- the LAB dependency probe and the
    WorkBuddy runtime probe -- and a lab pass exercises the first of those."""
    error = FileNotFoundError(2, "No such file or directory")
    error.filename = cwd
    raise error


def _check(raiser):
    """Run the readiness pass with every probe command failing the way `raiser` says."""
    with ExitStack() as stack:
        stack.enter_context(patch.object(preflight, "missing_runners", return_value=""))
        stack.enter_context(patch.object(preflight, "OpencodeSubstrate"))
        stack.enter_context(patch.object(config, "validate_models", lambda **_kw: None))
        # Forces the ADC probe, and names the interpreter the LAB dependency probe invokes.
        stack.enter_context(patch.object(config, "JUDGE_MODEL", "gemini-test"))
        stack.enter_context(patch.object(config, "LAB_PYTHON", "/venv/lab-judge/bin/python"))
        stack.enter_context(patch.object(preflight.shutil, "which", return_value=None))
        stack.enter_context(patch.object(preflight.subprocess, "run", side_effect=raiser))
        stack.enter_context(
            patch.object(preflight.subprocess, "check_output", return_value="0" * 40)
        )
        return preflight.check(_Adapter(), judge=True)


class ProbeFailureMessages(unittest.TestCase):
    def test_every_issue_names_the_path_that_could_not_be_used(self):
        errors = _check(_missing_program)
        self.assertTrue(errors, "the probe pass must record the failed checks")
        for error in errors:
            self.assertNotIn(
                "FileNotFoundError;",
                error,
                f"{error!r} names the exception class but not what is missing",
            )
        joined = "\n".join(errors)
        self.assertIn("Google Application Default Credentials:", joined)
        self.assertIn("No such file or directory (gcloud)", joined)

    def test_a_silent_probe_reports_the_command_and_the_wait_that_expired(self):
        joined = "\n".join(_check(_silent))
        self.assertIn("no response in 45s (git)", joined)
        self.assertIn("no response in 45s (gcloud)", joined)
        self.assertIn("no response in 45s (/venv/lab-judge/bin/python)", joined)

    def test_the_os_names_the_path_it_failed_on_even_when_that_path_is_the_directory(self):
        errors = _check(_missing_cwd)
        joined = "\n".join(errors)
        for error in errors:
            self.assertNotIn("FileNotFoundError;", error)
        # A probe that passes a cwd gets that directory named -- which is the actionable
        # path here, because a missing checkout is what harness2-setup has to fetch.
        self.assertIn(f"No such file or directory ({config.lab_repo()})", joined)
        # The patch probes pass no cwd, so the command itself is named.
        self.assertIn("No such file or directory (git)", joined)


if __name__ == "__main__":
    unittest.main()
