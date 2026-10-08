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

"""Parallel and sequential task-level recursion.

Each mode schedules 4k rollouts and 3k improvement stages, including shared base
executions. Invalid proposals receive one retry before inheriting the current harness.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .. import config
from . import context, prompts
from . import tree as harness_tree
from .ledger import Ledger
from .proposer import Proposer, ProposerError
from .spec import BenchmarkAdapter, RunView, resolve_prompts

STAGE_ATTEMPTS = 2
TRANSPORT_ATTEMPTS = config.TRANSPORT_ATTEMPTS
PHASE_WIDTH = config.PHASE_WIDTH


@dataclass
class Stage:
    """An improvement stage, including retries, its harness, and decision notes."""

    label: str
    dim: str
    tree: harness_tree.Tree
    kept_base: bool = False
    fell_back: bool = False

    error: str = ""
    usd: float = 0.0
    attempts: int = 0
    cached: bool = False
    notes: str = ""


@dataclass
class ModeResult:
    mode: str
    task_id: str
    k: int
    stages: list[Stage] = field(default_factory=list)
    trees: dict[str, harness_tree.Tree] = field(default_factory=dict)
    rollouts: dict[str, RunView] = field(default_factory=dict)
    finals: list[str] = field(default_factory=list)
    ledger: Ledger | None = None
    similarity: dict | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.finals) and not self.errors

    @property
    def fell_back(self) -> list[str]:
        return [s.label for s in self.stages if s.fell_back]

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "task_id": self.task_id,
            "k": self.k,
            "finals": self.finals,
            "ok": self.ok,
            "fell_back": self.fell_back,
            "kept_base": [s.label for s in self.stages if s.kept_base],
            "stages": [
                {
                    "label": s.label,
                    "dim": s.dim,
                    "fell_back": s.fell_back,
                    "kept_base": s.kept_base,
                    "attempts": s.attempts,
                    "usd": s.usd,
                    "cached": s.cached,
                    "notes": s.notes,
                    "error": (s.error or "")[:400],
                }
                for s in self.stages
            ],
            "similarity": self.similarity,
            "ledger": self.ledger.to_dict() if self.ledger else None,
            "errors": self.errors,
        }


def _accept_tree(
    surface,
    adapter: BenchmarkAdapter,
    new: harness_tree.Tree,
    fallback: harness_tree.Tree,
    label: str,
) -> tuple[bool, str]:
    """Repair then validate a proposed tree: (kept_base, "") if accepted, else the error."""
    triggered = harness_tree.autotrigger(surface, new)
    if triggered:
        print(f"  [{label}] auto-triggered skill(s): {', '.join(triggered)}", flush=True)
    changed = harness_tree.diff_summary(surface, fallback, new)
    kept = not (changed["added"] or changed["removed"] or changed["modified"])
    try:
        harness_tree.validate(
            surface,
            new,
            allow_subagents=adapter.spec.allow_subagents,
            reserved_scripts=harness_tree.RESERVED_SCRIPTS | adapter.spec.reserved_scripts,
        )
        adapter.validate_tree(new)
        context.assert_tree_has_no_grader_verdicts(new)
    except ValueError as exc:
        return kept, str(exc)
    return kept, ""


def _run_stage(
    *,
    label: str,
    dim: str,
    job: str,
    fallback: harness_tree.Tree,
    adapter: BenchmarkAdapter,
    prop: Proposer,
    ctx_dir: Path,
    task_id: str,
    rollouts: Sequence[RunView],
    harness_dir: Path | None,
    library_dir: Path | None,
    pool: Sequence[tuple[str, Path, RunView | None]] = (),
    decisions: dict[str, str] | None = None,
) -> Stage:
    """Build evidence, propose an edit, and retry an invalid proposal once."""
    surface = adapter.substrate.surface()

    try:
        tree = context.build(
            ctx_dir / label,
            adapter,
            task_id,
            rollouts,
            harness_dir=harness_dir,
            library_dir=library_dir,
            pool=pool,
            decisions=decisions,
        )
        mission = context.mission(tree, job=job)
    except Exception as exc:
        return Stage(
            label=label,
            dim=dim,
            tree=dict(fallback),
            fell_back=True,
            error=f"could not build evidence: {type(exc).__name__}: {exc}"[:400],
            attempts=0,
        )

    err = ""
    spent_usd = 0.0
    spent_calls = 0
    all_cached = True
    for attempt in range(1, STAGE_ATTEMPTS + 1):
        try:
            reply = prop.call(
                mission
                + (
                    f"\n\n# YOUR PREVIOUS ATTEMPT WAS REJECTED\n\n{err}\n\n"
                    f"Fix it in `context/harness/` and reply again."
                    if err
                    else ""
                ),
                tree.root,
                f"{label}.a{attempt}",
                harvest="context/harness",
                max_attempts=TRANSPORT_ATTEMPTS,
            )
        except ProposerError as exc:
            err = str(exc)
            spent_calls += exc.attempts or TRANSPORT_ATTEMPTS
            spent_usd += exc.cost_usd
            all_cached = False
            break
        spent_usd += reply.cost_usd
        spent_calls += 0 if reply.cached else max(1, reply.attempts)
        all_cached = all_cached and reply.cached

        if reply.workspace is None:
            err = (
                "the harness directory did not come back from the session; edit the files "
                "in `context/harness/` in place"
            )
            continue
        rejected: list[str] = []
        new = harness_tree.read_tree(surface, reply.workspace, rejected=rejected)
        if not new:
            err = "`context/harness/` came back empty -- edit the files, do not delete them all"
            continue
        if rejected:
            err = (
                "these files are off the editable surface and were not applied: "
                + ", ".join(sorted(rejected)[:6])
                + ". Component names must be lowercase letters, digits, `_` or `-` "
                f"(e.g. `{surface.skill_dir}/doc_review/SKILL.md`, "
                f"`scripts/check_totals.py`)."
            )
            continue

        kept, err = _accept_tree(surface, adapter, new, fallback, label)
        if err:
            harness_tree.write_tree(surface, new, tree.root / "harness")
            continue
        notes = re.search(r"<notes>(.*?)</notes>", reply.text, re.S)
        return Stage(
            label=label,
            dim=dim,
            tree=new,
            kept_base=kept,
            usd=spent_usd,
            attempts=spent_calls,
            cached=all_cached,
            notes=notes.group(1).strip() if notes else reply.text.strip(),
        )

    return Stage(
        label=label,
        dim=dim,
        tree=dict(fallback),
        fell_back=True,
        error=err[:400],
        usd=spent_usd,
        attempts=spent_calls,
        cached=all_cached,
    )


def _record_sessions(ledger: Ledger, stages: Sequence[Stage]) -> None:
    """Record stages in stable order after their concurrent phase finishes."""
    for stage in stages:
        ledger.session(stage.label, stage.usd, cached=stage.cached, attempts=stage.attempts)


def _roll(
    adapter: BenchmarkAdapter,
    ledger: Ledger,
    task_id: str,
    label: str,
    tree: harness_tree.Tree | None,
    run_id_of: Callable[[str], str],
    work_dir: Path,
    *,
    record: bool = True,
) -> RunView:
    """Materialise a harness and roll it once. A dead rollout is recorded, never raised."""
    hdir = None
    if tree is not None:
        hdir = harness_tree.write_tree(
            adapter.substrate.surface(), tree, work_dir / "harnesses" / label
        )
    run_id = run_id_of(label)
    try:
        rd = adapter.execute(task_id, hdir, run_id)
        exists = Path(rd).exists()
        rv = RunView(
            label=label,
            run_id=run_id,
            run_dir=rd,
            ok=exists,
            delivered=exists and adapter.delivered(rd),
            error="" if exists else "adapter returned a missing run directory",
        )
    except Exception as exc:
        rv = RunView(
            label=label,
            run_id=run_id,
            run_dir=work_dir / "missing",
            ok=False,
            delivered=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    if record:
        ledger.rollout(label)
    return rv


def _roll_many(
    adapter: BenchmarkAdapter,
    ledger: Ledger,
    task_id: str,
    items: Sequence[tuple[str, harness_tree.Tree]],
    run_id_of,
    work_dir: Path,
    *,
    width: int,
) -> dict[str, RunView]:
    """Roll independent harnesses concurrently, then record them in the caller's order.

    The ledger is written after the pool drains, so a concurrent run records the same sequence
    of labels as a serial one.
    """
    out: dict[str, RunView] = {}
    if not items:
        return out
    n = max(1, min(len(items), width))
    if n == 1:
        for label, tree in items:
            out[label] = _roll(
                adapter, ledger, task_id, label, tree, run_id_of, work_dir, record=True
            )
        return out
    with ThreadPoolExecutor(max_workers=n) as pool:
        got = list(
            pool.map(
                lambda it: _roll(
                    adapter, ledger, task_id, it[0], it[1], run_id_of, work_dir, record=False
                ),
                items,
            )
        )
    for (label, _tree), rv in zip(items, got):
        ledger.rollout(label)
        out[label] = rv
    return out


def run_parallel(
    task_id: str,
    k: int,
    base: Sequence[RunView],
    *,
    adapter: BenchmarkAdapter,
    prop: Proposer,
    work_dir: Path,
    run_id_of: Callable[[str], str],
    base_tree: harness_tree.Tree,
    library_dir: Path | None = None,
    propose_workers: int = PHASE_WIDTH,
    rollout_workers: int = PHASE_WIDTH,
) -> ModeResult:
    """WIDTH: 2k iid candidates from shared evidence, then k INDEPENDENT compositions."""
    surface = adapter.substrate.surface()
    ledger = Ledger(mode="agg", task_id=task_id, k=k)
    for r in base:
        ledger.rollout(r.label)
    result = ModeResult(mode="agg", task_id=task_id, k=k, ledger=ledger)
    result.rollouts.update({r.label: r for r in base})
    ctx_dir = work_dir / "context"
    benchmark_prompts = resolve_prompts(adapter, task_id)
    base_dir = harness_tree.write_tree(surface, base_tree, work_dir / "harnesses" / "base")

    specs = [(f"{d.lower()}{i}", d, i) for i in range(1, k + 1) for d in ("INV", "DOM")]
    with ThreadPoolExecutor(max_workers=min(len(specs), propose_workers)) as pool_x:
        stages = list(
            pool_x.map(
                lambda s: _run_stage(
                    label=s[0],
                    dim=s[1],
                    job=prompts.propose(benchmark_prompts, s[1], surface, k=k, index=s[2]),
                    fallback=base_tree,
                    adapter=adapter,
                    prop=prop,
                    ctx_dir=ctx_dir,
                    task_id=task_id,
                    rollouts=base,
                    harness_dir=base_dir,
                    library_dir=library_dir,
                ),
                specs,
            )
        )
    _record_sessions(ledger, stages)
    for stage in stages:
        result.stages.append(stage)
        result.trees[stage.label] = stage.tree
    result.rollouts.update(
        _roll_many(
            adapter,
            ledger,
            task_id,
            [(stage.label, stage.tree) for stage in stages],
            run_id_of,
            work_dir,
            width=rollout_workers,
        )
    )

    pool_ev = [
        (stage.label, work_dir / "harnesses" / stage.label, result.rollouts.get(stage.label))
        for stage in stages
    ]
    agg_specs = [f"agg{i}" for i in range(1, k + 1)]
    with ThreadPoolExecutor(max_workers=min(len(agg_specs), propose_workers)) as pool_x:
        aggs = list(
            pool_x.map(
                lambda lbl: _run_stage(
                    label=lbl,
                    dim="AGG",
                    job=prompts.aggregate(benchmark_prompts, surface, k=k, index=int(lbl[3:])),
                    fallback=base_tree,
                    adapter=adapter,
                    prop=prop,
                    ctx_dir=ctx_dir,
                    task_id=task_id,
                    rollouts=base,
                    harness_dir=base_dir,
                    library_dir=library_dir,
                    pool=pool_ev,
                    decisions={stage.label: stage.notes or stage.error for stage in stages},
                ),
                agg_specs,
            )
        )
    _record_sessions(ledger, aggs)
    for stage in aggs:
        result.stages.append(stage)
        result.trees[stage.label] = stage.tree
        result.finals.append(stage.label)
    result.rollouts.update(
        _roll_many(
            adapter,
            ledger,
            task_id,
            [(stage.label, stage.tree) for stage in aggs],
            run_id_of,
            work_dir,
            width=rollout_workers,
        )
    )

    result.similarity = harness_tree.diversity({s.label: s.tree for s in aggs})
    ledger.assert_identity()
    return result


def run_sequential(
    task_id: str,
    k: int,
    base: Sequence[RunView],
    *,
    adapter: BenchmarkAdapter,
    prop: Proposer,
    work_dir: Path,
    run_id_of: Callable[[str], str],
    base_tree: harness_tree.Tree,
    library_dir: Path | None = None,
    propose_workers: int = 2,
    rollout_workers: int = PHASE_WIDTH,
) -> ModeResult:
    """DEPTH: k nested passes over the same shared base evidence."""
    surface = adapter.substrate.surface()
    ledger = Ledger(mode="seq", task_id=task_id, k=k)
    for r in base:
        ledger.rollout(r.label)
    result = ModeResult(mode="seq", task_id=task_id, k=k, ledger=ledger)
    result.rollouts.update({r.label: r for r in base})
    ctx_dir = work_dir / "context"
    benchmark_prompts = resolve_prompts(adapter, task_id)

    current_harness = dict(base_tree)
    history: list[tuple[str, Path, RunView | None]] = []
    for t in range(1, k + 1):
        current_label = f"c{t - 1}" if t > 1 else "base"
        current_dir = harness_tree.write_tree(
            surface, current_harness, work_dir / "harnesses" / current_label
        )
        specs = [(f"inv{t}", "INV"), (f"dom{t}", "DOM")]
        with ThreadPoolExecutor(max_workers=min(2, propose_workers)) as pool_x:
            stages = list(
                pool_x.map(
                    lambda s: _run_stage(
                        label=s[0],
                        dim=s[1],
                        job=prompts.propose(
                            benchmark_prompts,
                            s[1],
                            surface,
                            k=k,
                            index=t,
                            round_t=(t if k > 1 else None),
                        ),
                        fallback=current_harness,
                        adapter=adapter,
                        prop=prop,
                        ctx_dir=ctx_dir,
                        task_id=task_id,
                        rollouts=base,
                        harness_dir=current_dir,
                        library_dir=library_dir,
                        pool=list(history),
                        decisions={
                            stage.label: stage.notes or stage.error for stage in result.stages
                        },
                    ),
                    specs,
                )
            )
        _record_sessions(ledger, stages)
        for stage in stages:
            result.stages.append(stage)
            result.trees[stage.label] = stage.tree
        result.rollouts.update(
            _roll_many(
                adapter,
                ledger,
                task_id,
                [(stage.label, stage.tree) for stage in stages],
                run_id_of,
                work_dir,
                width=rollout_workers,
            )
        )

        round_pool = list(history) + [
            (stage.label, work_dir / "harnesses" / stage.label, result.rollouts.get(stage.label))
            for stage in stages
        ]
        ck = f"c{t}"

        comp_job = (
            prompts.aggregate(benchmark_prompts, surface, k=1, index=1)
            if k == 1
            else prompts.compose_round(benchmark_prompts, surface, t=t, k=k)
        )
        comp = _run_stage(
            label=ck,
            dim="COMPOSE",
            job=comp_job,
            fallback=current_harness,
            adapter=adapter,
            prop=prop,
            ctx_dir=ctx_dir,
            task_id=task_id,
            rollouts=base,
            harness_dir=current_dir,
            library_dir=library_dir,
            pool=round_pool,
            decisions={stage.label: stage.notes or stage.error for stage in result.stages},
        )
        _record_sessions(ledger, [comp])
        result.stages.append(comp)
        result.trees[ck] = comp.tree
        result.rollouts[ck] = _roll(adapter, ledger, task_id, ck, comp.tree, run_id_of, work_dir)

        history = round_pool + [(ck, work_dir / "harnesses" / ck, result.rollouts[ck])]
        current_harness = comp.tree

    result.finals = [f"c{index}" for index in range(1, k + 1)]
    ledger.assert_identity()
    return result


def run_mode(mode: str, *a, **kw) -> ModeResult:
    """Dispatch on the on-disk mode token: "agg" (parallel) or "seq" (sequential)."""
    if mode == "agg":
        return run_parallel(*a, **kw)
    if mode == "seq":
        return run_sequential(*a, **kw)
    raise ValueError(
        f"unknown mode {mode!r}; the method has exactly two: "
        f"'agg' (parallel) and 'seq' (sequential)"
    )
