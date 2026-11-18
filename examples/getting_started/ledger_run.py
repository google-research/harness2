#!/usr/bin/env python3
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

"""Run Harness² over your own ledger CSVs and inspect the answers it saved.

A credential-free example (#55): it reuses the shipped demo adapter, so the
method machinery is real (Propose-Probe-Compose, blind selection, the library)
while the editor and the selector are scripted:

- the scripted editor drops the positive-only filter from `scripts/summarize.py`
  (the base harness sums only positive amounts -- a plausible-looking bug);
- the scripted selector picks the candidate whose total equals the SIGNED sum of
  the source ledger, i.e. selection is exact and independent of the editor.

Inputs are your CSVs (a header row `amount`, one signed amount per line), one
task per file, copied into the output directory so the run is self-contained.
Re-running with the same inputs resumes (finished tasks are skipped); changing a
CSV and re-running re-runs only that task: the summary's task fingerprint covers
the source material, so a stale answer is never silently reused.

    python examples/getting_started/ledger_run.py \
        --inputs examples/getting_started/inputs --output runs/ledger-example
    python examples/getting_started/ledger_run.py \
        --inputs examples/getting_started/inputs --output runs/ledger-example --inspect-only
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from decimal import Decimal
from pathlib import Path

try:  # normal when harness2 is installed (uv pip install -e .)
    from harness2.core import schedule
    from harness2.demo import DemoAdapter, DemoProposer
except ImportError:  # running straight from a source checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from harness2.core import schedule
    from harness2.demo import DemoAdapter, DemoProposer

TASK_FILE = "ledger.csv"


class LedgerAdapter(DemoAdapter):
    """The demo adapter fed from a directory of user CSVs, one task per file."""

    def __init__(self, root: Path, inputs: Path):
        super().__init__(root)
        self._ids = sorted(p.stem for p in inputs.glob("*.csv"))
        for task_id in self._ids:
            dest = self.task_files(task_id)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy(inputs / f"{task_id}.csv", dest / TASK_FILE)

    def tasks(self, split: str = "all") -> list[str]:
        return self._ids


def signed_total(csv_path: Path) -> Decimal:
    with csv_path.open() as fh:
        return sum((Decimal(row["amount"]) for row in csv.DictReader(fh)), Decimal(0))


def inspect(adapter: LedgerAdapter, runs_root: Path) -> int:
    """Read the answers the run saved and check them against the source sums."""
    failed = 0
    print(f"inspecting saved answers under {runs_root}")
    for task_id in adapter.tasks():
        expected = signed_total(adapter.task_files(task_id) / TASK_FILE)
        summary = runs_root / "demo-oc-agg1" / task_id / "summary.json"
        if not summary.is_file():
            print(f"  {task_id:<24} FAIL no summary at {summary}")
            failed += 1
            continue
        import json

        selections = json.loads(summary.read_text())["selections"]
        for mode in ("agg", "seq"):
            chosen = Path(selections[mode]["chosen_run_dir"]) / "answer.txt"
            if not chosen.is_file():
                print(f"  {task_id:<24} {mode} FAIL missing answer {chosen}")
                failed += 1
                continue
            got = chosen.read_text(encoding="utf-8").strip()
            ok = got == f"Total: {expected:.2f}"
            failed += not ok
            print(
                f"  {task_id:<24} {mode} {'PASS' if ok else 'FAIL'} "
                f"expected Total: {expected:.2f}  saved {got!r}"
            )
    return failed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.partition("\n\n")[0])
    p.add_argument("--inputs", required=True, type=Path, help="directory of <task>.csv files")
    p.add_argument("--output", required=True, type=Path, help="fresh output directory")
    p.add_argument("--inspect-only", action="store_true", help="skip running; just inspect")
    args = p.parse_args(argv)

    inputs = args.inputs
    if not inputs.is_dir() or not list(inputs.glob("*.csv")):
        p.error(f"--inputs needs a directory containing at least one .csv: {inputs}")
    args.output.mkdir(parents=True, exist_ok=True)

    adapter = LedgerAdapter(args.output, inputs)
    if not args.inspect_only:
        schedule.run_domain(
            "accounting",
            adapter.tasks(),
            1,
            adapter=adapter,
            prop=DemoProposer(adapter.spec.channels["proposer"]),
            runs_root=adapter.spec.runs_root,
            library_root=args.output / "library",
        )
    failed = inspect(adapter, adapter.spec.runs_root)
    if failed:
        print(f"{failed} answer(s) incorrect or missing", file=sys.stderr)
        return 1
    print("all saved answers match the signed source totals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
