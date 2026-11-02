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

"""After-the-fact answer-key audit of a finished sweep. Read-only; never calls a model.

The tripwires in core.context stop a task's grading text at the model boundary. Sweeps run
before them can only be checked here. Per task, the audit reports where the task's own key
appears: in an execution (a base or the measured run), in a context a model read, or in an
adopted harness. It then follows the library. A task that read a library entry from a
contaminated task in the same mode inherits the contamination, transitively, because an entry
carries a whole harness shaped by evidence that held the earlier task's key.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from . import context
from . import library as library_store
from .schedule import MODES, Namespace, _modes_of
from .spec import BenchmarkAdapter

FETCH_HOSTS = ("huggingface.co", "hf.co", "raw.githubusercontent.com", "github.com")
_URL = re.compile(r"https?://([A-Za-z0-9.-]+)[^\s\"'<>)\]`]*")
_MODEL_DIRS = ("context", "selection", "reflection")
FILENAME = "answer_key_audit.json"


def fetches(text: str, hosts: Sequence[str] = FETCH_HOSTS) -> list[str]:
    """URLs on the hosts docs/EXPERIMENTS.md says to block, in order of first appearance."""
    out: list[str] = []
    for m in _URL.finditer(text):
        host = m.group(1).lower().rstrip(".")
        if any(host == h or host.endswith("." + h) for h in hosts) and m.group(0) not in out:
            out.append(m.group(0))
    return out


@dataclass
class TaskAudit:
    task_id: str
    domain: str
    status: str
    keyed: bool
    executions: dict[str, str] = field(default_factory=dict)
    model_context: dict[str, list[str]] = field(default_factory=dict)
    adopted: dict[str, list[str]] = field(default_factory=dict)
    inherited: dict[str, list[str]] = field(default_factory=dict)
    fetches: list[str] = field(default_factory=list)
    read_library: dict[str, list[str]] = field(default_factory=dict)

    @property
    def direct(self) -> bool:
        return bool(
            self.executions or any(self.model_context.values()) or any(self.adopted.values())
        )

    def tainted_in(self, mode: str) -> bool:
        """Could this task's `mode` library entry carry a key? A base execution feeds every
        mode; a context or adopted harness only its own."""
        return bool(
            any(":" not in label for label in self.executions)
            or self.model_context.get(mode)
            or self.adopted.get(mode)
            or self.inherited.get(mode)
        )

    @property
    def verdict(self) -> str:
        if self.direct:
            return "contaminated"
        if any(self.inherited.values()):
            return "inherits"
        return "clean" if self.keyed else "unchecked"

    def to_dict(self) -> dict:
        out = asdict(self)
        out.pop("read_library")
        out["verdict"] = self.verdict
        return out


def _execution(adapter: BenchmarkAdapter, run_dir: str, key) -> tuple[str, list[str]]:
    """(where the key appears or "", blocked-host URLs) for one finished execution."""
    path = Path(run_dir or "")
    if not run_dir or not path.is_dir():
        return "", []
    where, urls = "", []
    for what, read in (
        ("trajectory", lambda: adapter.trajectory(path, obs_trunc=adapter.spec.obs_trunc)),
        ("deliverable", lambda: adapter.deliverable_text(path)),
    ):
        try:
            text = read() or ""
        except Exception:
            continue
        urls += fetches(text)
        hit = key.find(text)
        if hit and not where:
            where = f"{what} carries {hit.describe()}"
    return where, urls


def audit_task(
    adapter: BenchmarkAdapter,
    summary: Mapping,
    *,
    runs_root: Path,
    k: int,
    modes: Sequence[str] = MODES,
    seed: int | None = None,
    tag: str = "",
) -> TaskAudit:
    """Everything one task's own key touched, from its summary and work directories."""
    task_id = str(summary["task_id"])
    key = context.answer_key_for(adapter, task_id)
    out = TaskAudit(
        task_id=task_id,
        domain=str(summary.get("domain") or ""),
        status=str(summary.get("status") or ""),
        keyed=bool(key),
    )
    runs = {b.get("label", ""): b.get("run_dir", "") for b in summary.get("base") or []}
    selections = summary.get("selections") or {}
    for mode in modes:
        sel = selections.get(mode) or {}
        if sel.get("chosen_label"):
            runs[f"{mode}:{sel['chosen_label']}"] = sel.get("chosen_run_dir", "")
    for label, run_dir in runs.items():
        where, urls = _execution(adapter, run_dir, key)
        if where:
            out.executions[label] = where
        out.fetches += [u for u in urls if u not in out.fetches]

    bench, sub = adapter.spec.name, adapter.substrate.name
    recorded = _modes_of(summary)
    for mode in modes:
        if mode not in recorded:
            continue
        work = Namespace(bench, sub, mode, k, seed, tag).task_dir(runs_root, task_id)
        out.model_context[mode] = [
            f"{d}/{rel}: {hit.describe()}"
            for d in _MODEL_DIRS
            for rel, hit in context.answer_key_hits(work / d, key)
        ]
        label = (selections.get(mode) or {}).get("chosen_label")
        out.adopted[mode] = (
            [
                f"harnesses/{label}/{rel}: {hit.describe()}"
                for rel, hit in context.answer_key_hits(work / "harnesses" / label, key)
            ]
            if label
            else []
        )
        out.read_library[mode] = sorted(
            {p.name for p in (work / "context").glob("*/library/harnesses/*") if p.is_dir()}
        )
    return out


def propagate(audits: Sequence[TaskAudit]) -> None:
    """Mark tasks that read a library entry from a contaminated task, to a fixed point."""
    by_slug = {library_store.slug(a.task_id): a for a in audits}
    changed = True
    while changed:
        changed = False
        for a in audits:
            for mode, slugs in a.read_library.items():
                for s in slugs:
                    src = by_slug.get(s)
                    if src is None or src is a or not src.tainted_in(mode):
                        continue
                    got = a.inherited.setdefault(mode, [])
                    if src.task_id not in got:
                        got.append(src.task_id)
                        changed = True


def audit_namespace(
    adapter: BenchmarkAdapter,
    runs_root: Path,
    summary_ns: str,
    *,
    k: int,
    modes: Sequence[str] = MODES,
    seed: int | None = None,
    tag: str = "",
) -> list[TaskAudit]:
    """Audit every task summary under one namespace, then follow the library."""
    audits = []
    for path in sorted((Path(runs_root) / summary_ns).glob("*/summary.json")):
        try:
            summary = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! skipping unreadable {path}: {exc}", flush=True)
            continue
        if not summary.get("task_id"):
            continue
        audits.append(
            audit_task(adapter, summary, runs_root=runs_root, k=k, modes=modes, seed=seed, tag=tag)
        )
    propagate(audits)
    return audits


def write(audits: Sequence[TaskAudit], path: Path | str) -> Path:
    path = Path(path)
    counts: dict[str, int] = {}
    for a in audits:
        counts[a.verdict] = counts.get(a.verdict, 0) + 1
    path.write_text(
        json.dumps({"counts": counts, "tasks": [a.to_dict() for a in audits]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def render(audits: Sequence[TaskAudit], *, title: str = "") -> str:
    lines = [title, "=" * max(40, len(title))] if title else []
    for a in audits:
        if a.verdict == "clean":
            continue
        lines.append(f"  {a.verdict:<12} {a.task_id}")
        for label, where in a.executions.items():
            lines.append(f"      execution {label}: {where}")
        for mode, hits in a.model_context.items():
            lines += [f"      {mode} model context {h}" for h in hits[:4]]
            if len(hits) > 4:
                lines.append(f"      {mode} model context ... and {len(hits) - 4} more file(s)")
        for mode, hits in a.adopted.items():
            lines += [f"      {mode} adopted {h}" for h in hits[:4]]
        for mode, src in a.inherited.items():
            if src:
                lines.append(f"      {mode} read library entries of: {', '.join(src)}")
    for a in audits:
        if a.fetches:
            shown = ", ".join(a.fetches[:3]) + (" ..." if len(a.fetches) > 3 else "")
            lines.append(f"  fetched      {a.task_id}: {shown}")
    counts = {v: sum(1 for a in audits if a.verdict == v) for v in ("contaminated", "inherits")}
    unchecked = sum(1 for a in audits if a.verdict == "unchecked")
    lines += [
        "",
        f"  {len(audits)} task(s): {counts['contaminated']} carry their own answer key, "
        f"{counts['inherits']} inherit one through the library"
        + (f", {unchecked} unchecked (the adapter supplies no answer key)" if unchecked else ""),
    ]
    if any(a.fetches for a in audits):
        lines.append(
            "  'fetched' lists URLs on hosts docs/EXPERIMENTS.md#web-access says to block; a "
            "fetch is not a leak by itself, so open the trajectory before excluding the task."
        )
    return "\n".join(lines)


__all__ = [
    "FETCH_HOSTS",
    "FILENAME",
    "TaskAudit",
    "audit_namespace",
    "audit_task",
    "fetches",
    "propagate",
    "render",
    "write",
]
