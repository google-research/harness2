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

"""A deterministic local adapter for exercising the real adaptation workflow."""

from __future__ import annotations

import csv
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from .core import tree
from .core.proposer import Proposer, Reply
from .core.spec import BenchPrompts, BenchSpec, ChannelSpec
from .substrate.opencode import OpencodeSubstrate

BASE_SCRIPT = """import csv
import sys
from decimal import Decimal

with open(sys.argv[1]) as source:
    amounts = [Decimal(row['amount']) for row in csv.DictReader(source)]
total = sum((amount for amount in amounts if amount > 0), Decimal(0))
print(f'Total: {total:.2f}')
"""


class DemoAdapter:
    def __init__(self, root: Path):
        self.root = root
        self.substrate = OpencodeSubstrate()
        channel = ChannelSpec("opencode", "local/deterministic")
        self.spec = BenchSpec(
            name="demo",
            runs_root=root / "runs",
            install_mode="workspace_config",
            prompts=BenchPrompts(work="reconcile signed amounts in a transaction ledger"),
            channels={"proposer": channel, "selector": channel},
            leak_globs=("reference_answer.txt",),
        )
        self.execution_count = 0
        base = {name: "" for name in self.substrate.surface().always_on}
        base["systemprompt.md"] = "Reconcile the ledger using python scripts/summarize.py.\n"
        base["scripts/summarize.py"] = BASE_SCRIPT
        tree.write_tree(self.substrate.surface(), base, self.base_harness(), stubs=False)
        for task_id, amounts in (
            ("ledger-1", "100.25\n-20.10\n5.00\n"),
            ("ledger-2", "35.50\n-10.25\n-5.00\n"),
        ):
            source = self.task_files(task_id)
            source.mkdir(parents=True, exist_ok=True)
            (source / "ledger.csv").write_text("amount\n" + amounts, encoding="utf-8")

    def tasks(self, split: str = "all") -> list[str]:
        return ["ledger-1", "ledger-2"]

    def domain_of(self, task_id: str) -> str:
        return "accounting"

    def task_prompt(self, task_id: str) -> str:
        return "Sum every signed amount in ledger.csv. Return Total: with two decimal places."

    def task_files(self, task_id: str) -> Path:
        return self.root / "sources" / task_id

    def base_harness(self) -> Path:
        return self.root / "base"

    def validate_tree(self, harness: tree.Tree) -> None:
        if "scripts/summarize.py" not in harness:
            raise ValueError("The local executor requires scripts/summarize.py")

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        self.execution_count += 1
        run_dir = self.root / "executions" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        script = (harness_dir or self.base_harness()) / "scripts/summarize.py"
        result = subprocess.run(
            [sys.executable, str(script), str(self.task_files(task_id) / "ledger.csv")],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout or 10,
        )
        (run_dir / "answer.txt").write_text(result.stdout, encoding="utf-8")
        (run_dir / "trajectory.md").write_text(
            "[ACTION] Run scripts/summarize.py on ledger.csv\n[OBSERVATION]\n" + result.stdout,
            encoding="utf-8",
        )
        return run_dir

    def delivered(self, run_dir: Path) -> bool:
        return (run_dir / "answer.txt").is_file()

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        return (run_dir / "trajectory.md").read_text(encoding="utf-8")

    def deliverable_text(self, run_dir: Path) -> str:
        return (run_dir / "answer.txt").read_text(encoding="utf-8")

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        return {"answer.txt": self.deliverable_text(run_dir)}


class DemoProposer(Proposer):
    """Scripted edits and selection; no model service or benchmark judge is used."""

    def call(self, mission, context_dir, label, **kwargs) -> Reply:
        context = Path(context_dir)
        workspace = None
        if label.startswith("select."):
            with (context / "source/ledger.csv").open() as source:
                expected = sum(
                    (Decimal(row["amount"]) for row in csv.DictReader(source)), Decimal(0)
                )
            candidates = sorted((context / "candidates").glob("*.md"))
            chosen = next(
                path.stem for path in candidates if f"Total: {expected:.2f}" in path.read_text()
            )
            text = (
                f"<why>The signed total matches the source ledger.</why>\n```choice\n{chosen}\n```"
            )
        elif label.startswith("experience."):
            changed = (context / "harness/scripts/summarize.py").read_text() != (
                context / "base_harness/scripts/summarize.py"
            ).read_text()
            text = (
                "<edits>\ncomponent: scripts/summarize.py\ncandidates: inv1\n"
                "change: dropped the positive-only filter from the amount sum\n"
                "observed: the probe total included the credits the base run omitted\n"
                f"verdict: {'helped' if changed else 'no observed effect'}\n</edits>\n\n"
            ) + (
                "<experience>\ncue: When a ledger includes credits and debits\n"
                "lesson: Include both signs when summing in scripts/summarize.py.\n"
                "evidence: The scripts/summarize.py row -- removing the positive-only filter "
                "changed the probe total to include the credits omitted by the base trajectory.\n"
                "confidence: high for this observed arithmetic contrast\n</experience>"
                if changed
                else "NO_ENTRY"
            )
        else:
            workspace = context / "harness"
            script = workspace / "scripts/summarize.py"
            script.write_text(script.read_text().replace(" if amount > 0", ""), encoding="utf-8")
            text = "<notes>Keep signed amounts: the base total omitted credits in the source ledger.</notes>"
        reply = Reply(label=label, text=text, ok=True, attempts=1, workspace=workspace)
        self._calls.append(reply)
        return reply
