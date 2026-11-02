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

"""Independent recursion modes must resume independently of unrequested failures."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.core import schedule
from harness2.core.proposer import ProposerError
from harness2.demo import DemoAdapter, DemoProposer


class RequestedModeResumeTests(unittest.TestCase):
    def test_valid_parallel_mode_resumes_when_sequential_selection_failed(self):
        class SequentialFailure(DemoProposer):
            def call(self, mission, context_dir, label, **kwargs):
                if label == "select.seq2":
                    raise ProposerError("temporary sequential selector failure", attempts=1)
                return super().call(mission, context_dir, label, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = DemoAdapter(root)
            proposer = SequentialFailure(adapter.spec.channels["proposer"])

            def run(modes):
                return schedule.run_domain(
                    "accounting",
                    ["ledger-1"],
                    2,
                    adapter=adapter,
                    prop=proposer,
                    runs_root=adapter.spec.runs_root,
                    library_root=root / "library",
                    modes=modes,
                )

            self.assertEqual(run(("agg", "seq"))["tasks"][0]["status"], "partial")
            summary = adapter.spec.runs_root / "demo-oc-agg2/ledger-1/summary.json"
            record = json.loads(summary.read_text())
            self.assertTrue(record["selections"]["agg"]["ok"])
            self.assertFalse(record["selections"]["seq"]["ok"])
            counts = (adapter.execution_count, len(proposer._calls))
            for _ in range(2):
                self.assertEqual(run(("agg",))["tasks"][0]["status"], "skipped")
                self.assertEqual((adapter.execution_count, len(proposer._calls)), counts)
            # Requesting the failed mode still retries rather than claiming success.
            self.assertEqual(run(("seq",))["tasks"][0]["status"], "partial")
            self.assertGreater(adapter.execution_count, counts[0])
