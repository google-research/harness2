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

"""The library keeps a ledger of what each component did.

The reflector already records a per-component verdict for every edit it saw.
Until now those verdicts sat in the entry as free text under "What each edit did",
and the INDEX the improver browses showed only how MANY components an entry had --
so a component the reflector recorded as `hurt` looked exactly like one that
`helped`.
"""

import tempfile
import unittest
from pathlib import Path

from harness2.core import library, schedule
from harness2.demo import DemoAdapter, DemoProposer

EDITS = (
    "component: scripts/summarize.py\n"
    "candidates: inv1\n"
    "change: dropped the positive-only filter\n"
    "observed: the probe total included the credits\n"
    "verdict: helped\n"
    "component: skills/audit/SKILL.md\n"
    "candidates: inv2, dom1\n"
    "change: added a reconciliation step\n"
    "observed: the run took two attempts\n"
    "verdict: hurt\n"
    "component: skills/audit/SKILL.md\n"
    "candidates: dom2\n"
    "change: narrowed the trigger line\n"
    "observed: nothing changed\n"
    "verdict: no observed effect\n"
)


class VerdictLedgerTests(unittest.TestCase):
    def test_the_rows_become_a_per_component_ledger(self):
        got = library.parse_verdicts(EDITS)
        self.assertEqual(
            got,
            {
                "scripts/summarize.py": ["helped"],
                "skills/audit/SKILL.md": ["hurt", "no observed effect"],
            },
        )

    def test_the_summary_counts_the_rows_of_one_component(self):
        summary = library.verdict_summary(library.parse_verdicts(EDITS))
        self.assertEqual(
            summary,
            "scripts/summarize.py helped; skills/audit/SKILL.md hurt/no observed effect",
        )

    def test_a_repeated_verdict_is_counted(self):
        edits = (
            "component: scripts/x.py\nverdict: helped\n"
            "component: scripts/x.py\nverdict: helped\n"
        )
        self.assertEqual(
            library.verdict_summary(library.parse_verdicts(edits)), "scripts/x.py helped x2"
        )

    def test_a_row_without_a_component_is_ignored(self):
        self.assertEqual(library.parse_verdicts("candidates: inv1\nverdict: helped\n"), {})

    def test_an_empty_block_is_no_ledger(self):
        self.assertEqual(library.parse_verdicts(""), {})
        self.assertEqual(library.verdict_summary({}), "")

    def test_the_index_carries_the_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            adapter = DemoAdapter(root)
            proposer = DemoProposer(adapter.spec.channels["proposer"])
            schedule.run_domain(
                "accounting",
                adapter.tasks(),
                2,
                adapter=adapter,
                prop=proposer,
                runs_root=adapter.spec.runs_root,
                library_root=root / "library" / "2",
            )
            index = (root / "library" / "2" / "agg" / "accounting" / "INDEX.md").read_text()
            self.assertIn("- verdicts: scripts/summarize.py helped", index)
            entry = (
                root
                / "library"
                / "2"
                / "agg"
                / "accounting"
                / "harnesses"
                / "ledger-1"
                / "EXPERIENCE.md"
            ).read_text()
            self.assertIn("- **verdicts**: scripts/summarize.py helped", entry)
            # the raw rows are still there
            self.assertIn("verdict: helped", entry)


if __name__ == "__main__":
    unittest.main()
