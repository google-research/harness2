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

"""Readiness dispatch for the executor substrate vs the improver's OpenCode checkout.

`preflight.check` runs the OpenCode substrate's own preflight unconditionally because the
improver is executed through OpenCode whatever the executor substrate is. For an OpenCode
executor the adapter's own preflight makes that same call, so one defect was reported twice
and `harness2-check` counted it as two issues.
"""

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from harness2 import config, preflight


class _Substrate:
    def __init__(self, name, sink):
        self.name = name
        self.sink = sink

    def preflight(self, **_kwargs):
        self.sink.append(self.name)


class _Adapter:
    def __init__(self, name, substrate, sink):
        class _Spec:
            pass

        spec = _Spec()
        spec.name = name
        self.spec = spec
        self.substrate = substrate
        self.sink = sink

    def tasks(self, split="all"):
        return ["t"]

    def preflight(self):
        self.sink.append(f"adapter:{self.substrate.name}")


class SubstratePreflightDispatch(unittest.TestCase):
    """The standalone OpenCode probe runs only when the executor is not OpenCode."""

    def _check(self, substrate_name):
        sink = []
        adapter = _Adapter("lab", _Substrate(substrate_name, sink), sink)
        with ExitStack() as stack:
            stack.enter_context(patch.object(preflight, "missing_runners", return_value=""))
            oc = stack.enter_context(patch.object(preflight, "OpencodeSubstrate"))
            stack.enter_context(patch.object(config, "validate_models", lambda **_kw: None))
            # The probe() bodies shell out to the vendor checkouts; this test is about which
            # validate() callables are dispatched, not about what an unconfigured tree says.
            stack.enter_context(patch.object(preflight.subprocess, "run"))
            stack.enter_context(patch.object(preflight.subprocess, "check_output", return_value=""))
            stack.enter_context(patch.object(preflight.shutil, "which", return_value="/bin/x"))
            errors = preflight.check(adapter, judge=True)
        return errors, oc, sink

    def test_opencode_executor_does_not_probe_opencode_twice(self):
        _errors, oc, sink = self._check("oc")
        self.assertEqual(sink, ["adapter:oc"], "the adapter's own preflight must still run")
        oc.assert_not_called()

    def test_codex_executor_still_demands_the_improver_opencode_checkout(self):
        _errors, oc, sink = self._check("cx")
        self.assertEqual(sink, ["adapter:cx"])
        oc.assert_called_once_with()
        oc.return_value.preflight.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
