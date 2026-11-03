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

"""Extend harness2-demo with CSV inputs and read-only checks of saved outputs.

Run from the source checkout with the package installed. Editors and selection
are scripted; the workflow executes local Python, never a model service.
"""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from harness2.core.schedule import MODE_NAMES, mode_token, run_domain
from harness2.demo import DemoAdapter, DemoProposer


def read_amounts(path: Path) -> list[Decimal]:
    """Accept one amount column, finite values and at most two decimal places."""
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != ["amount"]:
            raise ValueError(f"{path}: expected exactly one column named amount")
        amounts = []
        for line, row in enumerate(reader, 2):
            try:
                value = Decimal(row["amount"])
                valid = (
                    len(row) == 1 and value.is_finite() and value == value.quantize(Decimal("0.01"))
                )
            except (InvalidOperation, TypeError):
                valid = False
            if not valid:
                raise ValueError(f"{path}:{line}: expected a finite amount in cents")
            amounts.append(value)
    if not amounts:
        raise ValueError(f"{path}: add at least one amount")
    return amounts


class LedgerAdapter(DemoAdapter):
    """Keep the demo executor and task contract; supply a stream of CSV ledgers.

    A different kind of job needs its own BenchmarkAdapter implementation.
    This subclass deliberately keeps the demo's signed-sum task and known fix.
    """

    def __init__(self, root: Path, ledgers: list[list[Decimal]]):
        super().__init__(root)
        self.task_ids = [f"ledger-{index}" for index in range(1, len(ledgers) + 1)]
        for task_id, amounts in zip(self.task_ids, ledgers, strict=True):
            source = self.task_files(task_id)
            source.mkdir(parents=True, exist_ok=True)
            (source / "ledger.csv").write_text(
                "amount\n" + "".join(f"{amount:.2f}\n" for amount in amounts), encoding="utf-8"
            )

    def tasks(self, split: str = "all") -> list[str]:
        return self.task_ids.copy()


def run(input_dir: Path, output: Path, k: int, mode: str) -> int:
    if k < 1:
        raise ValueError("--k must be positive")
    inputs = sorted(input_dir.glob("*.csv"))
    if not inputs:
        raise ValueError(f"{input_dir}: no *.csv files found")
    ledgers = [read_amounts(path) for path in inputs]
    root = output.expanduser().resolve()
    # No resume here: changed inputs must never reuse results from an old task.
    if root.exists():
        raise ValueError(f"{root}: already exists; choose a new --output directory")
    root.mkdir(parents=True)
    adapter = LedgerAdapter(root, ledgers)
    proposer = DemoProposer(adapter.spec.channels["proposer"])
    modes = ("agg", "seq") if mode == "both" else (mode_token(mode),)
    for task_id, path in zip(adapter.tasks(), inputs, strict=True):
        print(f"{task_id} <- {path.name}")
    result = run_domain(
        "accounting",
        adapter.tasks(),
        k,
        adapter=adapter,
        prop=proposer,
        runs_root=adapter.spec.runs_root,
        library_root=root / "library" / f"k{k}",
        modes=modes,
    )
    result["execution_count"] = adapter.execution_count
    (root / "demo.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Local Python executions: {adapter.execution_count}; model calls: 0")
    if result["failed"]:
        return 1
    return int(not inspect(root))


def inspect(root: Path) -> bool:
    """Compare actual answer files to source sums, without rerunning the adapter."""
    root = root.expanduser().resolve()
    manifest = json.loads((root / "demo.json").read_text(encoding="utf-8"))
    paths = sorted(root.glob("runs/demo-oc-agg*/*/summary.json"))
    summaries = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    task_ids = {row["task"] for row in manifest["tasks"]}
    if not task_ids or {summary["task_id"] for _, summary in summaries} != task_ids:
        raise ValueError(f"{root}: missing task summaries; rerun in a fresh output directory")
    passed = manifest["failed"] == 0 and manifest["done"] == manifest["n_tasks"]
    print(f"\n{root.name}: scripted demo; checking saved files, no model quality measurement")
    for path, summary in summaries:
        task_id, k = summary["task_id"], summary["k"]
        expected = f"Total: {sum(read_amounts(root / 'sources' / task_id / 'ledger.csv')):.2f}"
        base_dir = Path(summary["base"][0]["run_dir"])
        base = (base_dir / "answer.txt").read_text(encoding="utf-8").strip()
        if not summary["modes"]:
            raise ValueError(f"{path}: no completed modes")
        for mode, record in summary["modes"].items():
            selection = summary["selections"][mode]
            answer_path = Path(selection["chosen_run_dir"]) / "answer.txt"
            answer = answer_path.read_text(encoding="utf-8").strip()
            ledger = record["ledger"]
            ok = (
                summary["status"] == "ok"
                and record["ok"]
                and selection["ok"]
                and answer == expected
                and (ledger["R"], ledger["P"]) == (4 * k, 3 * k)
            )
            passed = passed and ok
            print(
                f"{task_id} {MODE_NAMES[mode]} k={k}: "
                f"base={base}; selected={answer}; expected={expected}; "
                f"R={ledger['R']} P={ledger['P']} {'PASS' if ok else 'FAIL'}"
            )
            print(f"  answer: {answer_path}")
        print(f"  summary: {path}")
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    runner = subparsers.add_parser("run", help="run custom ledgers with scripted editors")
    runner.add_argument("--input-dir", type=Path, required=True)
    runner.add_argument("--output", type=Path, required=True)
    runner.add_argument("--k", type=int, default=1)
    runner.add_argument("--mode", choices=("parallel", "sequential", "both"), default="both")
    reader = subparsers.add_parser("inspect", help="check saved outputs without executing code")
    reader.add_argument("roots", type=Path, nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return run(args.input_dir, args.output, args.k, args.mode)
        results = [inspect(root) for root in args.roots]
        return int(not all(results))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
