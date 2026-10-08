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

"""Choose an existing anonymized final output, then reflect on its refinement evidence."""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from . import context
from . import prompts as P
from .proposer import Proposer, ProposerError
from .spec import BenchmarkAdapter, BenchPrompts, RunView, resolve_prompts

_CHOICE = re.compile(r"```choice\s*\n?\s*([A-Za-z0-9_-]+)\s*\n?\s*```", re.S)
_WHY = re.compile(r"<why>(.*?)</why>", re.S)
_EXP = re.compile(r"<experience>(.*?)</experience>", re.S)
_EDITS = re.compile(r"<edits>(.*?)</edits>", re.S)

_T = TypeVar("_T")


def order_graded_first(items: Sequence[_T], graded: Callable[[_T], bool]) -> list[_T]:
    """Graded items first, in their original relative order; nothing is discarded."""
    return sorted(items, key=lambda x: 0 if graded(x) else 1)


@dataclass
class Selection:
    task_id: str
    mode: str
    k: int
    chosen_id: str = ""
    chosen_label: str = ""
    chosen_run_id: str = ""
    chosen_is_base: bool = False
    why: str = ""
    id_map: dict[str, str] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    n_candidates: int = 0
    ok: bool = False
    fallback: bool = False
    error: str = ""
    usd: float = 0.0
    experience: dict = field(default_factory=dict)
    trivial: bool = False
    evidence: str = "deliverable"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def assign_ids(labels: Sequence[str], *, task_id: str, k: int, mode: str) -> dict[str, str]:
    """anon id -> label, by canonical sort then a seeded shuffle: stable across reruns."""
    seed = int(hashlib.sha1(f"{task_id}|{k}|{mode}".encode()).hexdigest()[:12], 16)
    ordered = sorted(labels)
    random.Random(seed).shuffle(ordered)
    return {_anon_id(i): lbl for i, lbl in enumerate(ordered)}


def _anon_id(index: int) -> str:
    """A..Z, AA..AZ, BA... -- always accepted by the choice parser."""
    out = ""
    n = int(index) + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _is_base(label: str) -> bool:
    return label.startswith("base")


def selection_pool(
    mode: str, k: int, rollouts: dict[str, RunView], base: Sequence[RunView]
) -> dict[str, RunView]:
    """The one home for selection-pool construction: exactly the k finals of `mode`.

    Labels are derived from k -- agg1..aggk for the parallel mode, c1..ck for the sequential
    one -- so the reference executions are not in the pool and there is no abstention by
    construction. This is the published convention and the only pool this release builds.

    `base` is accepted and deliberately unused: it is what the pool excludes, and the call
    site passes it so that exclusion is visible where the pool is built.
    """
    del base
    labels = (
        [f"agg{i}" for i in range(1, int(k) + 1)]
        if mode == "agg"
        else [f"c{t}" for t in range(1, int(k) + 1)]
    )
    return {lbl: rollouts[lbl] for lbl in labels if lbl in rollouts}


def _render_pool(
    candidates: dict[str, RunView], adapter: BenchmarkAdapter, sel: Selection
) -> tuple[dict[str, RunView], dict[str, str]]:
    """Drop non-answers before anonymising, recording each drop and its cause in `dropped`."""
    pool: dict[str, RunView] = {}
    artifacts: dict[str, str] = {}
    for lbl, rv in candidates.items():
        if not (rv.ok and rv.delivered):
            sel.dropped.append(lbl)
            continue
        try:
            rendered = adapter.deliverable_text(rv.run_dir) or ""
        except Exception as exc:
            sel.dropped.append(f"{lbl} (render failed: {type(exc).__name__}: {exc})"[:200])
            continue
        if not rendered.strip():
            sel.dropped.append(lbl)
            continue
        pool[lbl] = rv
        artifacts[lbl] = rendered
    if not pool and candidates and adapter.spec.state_deliverable:
        for lbl, rv in candidates.items():
            if not rv.ok:
                continue
            try:
                body = adapter.trajectory(rv.run_dir, obs_trunc=adapter.spec.obs_trunc)
            except Exception as exc:
                sel.dropped.append(f"{lbl} (trajectory failed: {type(exc).__name__}: {exc})"[:200])
                continue
            if not body.strip() or body.startswith("(no trajectory"):
                continue
            pool[lbl] = rv
            artifacts[lbl] = (
                "(this task produced no file deliverable; the candidate is "
                "shown as its own session transcript)\n\n" + body
            )
        if pool:
            sel.evidence = "trajectory"
            sel.dropped = [d for d in sel.dropped if d not in pool]
    return pool, artifacts


_DEFAULT_ASSETS_NOTE = (
    "     Rendered evidence for this candidate is in `{anon}/`: {names}. "
    "OPEN THESE -- they show what the artifact actually does when run, "
    "which its source alone does not tell you.\n"
)
_DEFAULT_ASSETS_LINE = (
    "    context/candidates/<id>/     RENDERED EVIDENCE for that candidate "
    "({shown}) -- what the\n"
    "                                artifact actually DOES when run. Source "
    "alone does not show this.\n"
)


def _assets_note(adapter: BenchmarkAdapter, anon: str, names: Sequence[str]) -> str:
    fn = getattr(adapter, "deliverable_assets_note", None)
    if callable(fn):
        return str(fn(list(names)) or "").replace("{anon}", anon)
    return _DEFAULT_ASSETS_NOTE.format(anon=anon, names=", ".join(names))


def _assets_line(adapter: BenchmarkAdapter, shown: str) -> str:
    fn = getattr(adapter, "deliverable_assets_line", None)
    if callable(fn):
        return str(fn(shown) or "")
    return _DEFAULT_ASSETS_LINE.format(shown=shown)


def _write_candidates(
    sel: Selection,
    artifacts: dict[str, str],
    pool: dict[str, RunView],
    cand_dir: Path,
    adapter: BenchmarkAdapter,
) -> set[str]:
    """Write one anonymised, untrimmed file per candidate; returns the asset names copied in.

    The adapter names the asset files explicitly and only those are copied: a path into the
    run dir would also carry the grader's own output.
    """
    asset_fn = getattr(adapter, "deliverable_assets", None)
    asset_names: set[str] = set()
    for anon, lbl in sel.id_map.items():
        text = artifacts[lbl]
        assets: list[Path] = []
        if callable(asset_fn):
            assets = [Path(a) for a in (asset_fn(pool[lbl].run_dir) or []) if Path(a).is_file()]
        note = ""
        if assets:
            adir = cand_dir / anon
            adir.mkdir(parents=True, exist_ok=True)
            names = []
            for a in assets:
                shutil.copy2(a, adir / a.name)
                names.append(a.name)
            note = _assets_note(adapter, anon, names)
            asset_names.update(names)
        header = (
            f"<!-- candidate {anon}: {len(text):,} chars, {text.count(chr(10)) + 1:,} lines. "
            f"COMPLETE -- nothing trimmed. Navigate with grep/offsets; evaluating a "
            f"candidate means opening the part that determines whether it works.\n"
            f"{note}-->\n"
        )
        (cand_dir / f"{anon}.md").write_text(header + text, encoding="utf-8")
    return asset_names


def _evidence_block(
    asset_names: set[str], adapter: BenchmarkAdapter, reference_only: bool
) -> tuple[str, str]:
    """The assets wording for the selector prompt and for its evidence list."""
    if not asset_names:
        return "", ""
    shown = ", ".join(sorted(asset_names)[:4])
    a_evid = (
        "context/candidates/<id>/        "
        + ("raw originals per candidate" if reference_only else "rendered evidence per candidate")
        + f": {shown}\n"
    )
    return _assets_line(adapter, shown), a_evid


def _fallback(sel: Selection, pool: dict[str, RunView], pool_kind: str, exc: Exception) -> bool:
    """The neutral stand-in after a selector failure: a delivered base, else the first final."""
    base = sorted(label for label in pool if _is_base(label))
    if base:
        sel.chosen_label = base[0]
    elif pool_kind == "finals":
        sel.chosen_label = sorted(pool)[0]
    else:
        sel.error = (
            str(exc)[:350] + "; no delivered base artifact was available for a neutral fallback"
        )
        return False
    sel.chosen_id = next((a for a, label in sel.id_map.items() if label == sel.chosen_label), "")
    sel.fallback = True
    sel.error = str(exc)[:400]
    return True


def _persist(sel: Selection, root: Path, run_dir: str) -> None:
    """Write selection.json, carrying a disk failure on the record rather than raising."""
    try:
        rec = sel.to_dict()
        rec["chosen_run_dir"] = run_dir
        (root / "selection.json").write_text(
            json.dumps(rec, indent=2, default=str), encoding="utf-8"
        )
    except OSError as exc:
        sel.ok = False
        sel.error = (sel.error + f" | could not persist selection: {exc}")[:400]


def run_selection(
    task_id: str,
    k: int,
    mode: str,
    candidates: dict[str, RunView],
    *,
    adapter: BenchmarkAdapter,
    prop: Proposer,
    work_dir: Path,
    pool_kind: str = "mixed",
) -> Selection:
    """Pick one output. A selector failure falls back to a delivered base and never raises;
    an adapter bug does raise, because it is a bug and not a selection outcome.

    `pool_kind="finals"` adjusts only the wording shown to the selector and the failure
    fallback; the default is byte-identical to the mixed-pool behaviour.
    """
    sel = Selection(task_id=task_id, mode=mode, k=k)
    bp = resolve_prompts(adapter, task_id)

    pool, artifacts = _render_pool(candidates, adapter, sel)
    sel.n_candidates = len(pool)
    if not pool:
        sel.error = "no candidate produced an artifact"
        return sel

    sel.id_map = assign_ids(list(pool), task_id=task_id, k=k, mode=mode)

    root = Path(work_dir) / "selection" / f"{mode}{k}"

    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    (root / "task.md").write_text(adapter.task_prompt(task_id), encoding="utf-8")
    context.copy_source(adapter, task_id, root)

    cand_dir = root / "candidates"
    cand_dir.mkdir(exist_ok=True)
    asset_names = _write_candidates(sel, artifacts, pool, cand_dir, adapter)

    ids = ", ".join(sorted(sel.id_map))
    reference_only = bool(getattr(adapter, "deliverable_assets_reference_only", False))
    a_line, a_evid = _evidence_block(asset_names, adapter, reference_only)
    mission = "\n\n".join(
        [
            P.select(bp, len(pool), pool_kind=pool_kind, assets_note=a_line),
            "# YOUR EVIDENCE\n\nIt is on disk. Read it -- do not answer from this message alone.\n\n"
            "context/task.md                 the instructions\n"
            "context/source/                 task source materials, when supplied\n"
            f"context/candidates/<id>.md      one file per candidate: {ids}\n"
            + a_evid
            + (
                "\nOpen every candidate before choosing"
                + (
                    ", INCLUDING the rendered evidence in its directory"
                    if asset_names and not reference_only
                    else ""
                )
                + f". Choose exactly one of: {ids}."
            ),
        ]
    )

    context.assert_no_grader_verdicts(root)

    try:
        reply = prop.call(mission, root, f"select.{mode}{k}")
        sel.usd += reply.cost_usd
        m = _CHOICE.search(reply.text)
        if not m:
            raise ValueError("no ```choice block in the reply")
        anon = m.group(1).strip()
        if anon not in sel.id_map:
            raise ValueError(f"chose {anon!r}, which is not one of {ids}")
        w = _WHY.search(reply.text)
        sel.why = (w.group(1).strip() if w else "")[:2000]
        sel.chosen_id = anon
        sel.chosen_label = sel.id_map[anon]
        sel.ok = True
    except (ProposerError, ValueError) as exc:
        if isinstance(exc, ProposerError):
            sel.usd += exc.cost_usd
        if not _fallback(sel, pool, pool_kind, exc):
            return sel

    sel.chosen_run_id = pool[sel.chosen_label].run_id
    sel.chosen_is_base = _is_base(sel.chosen_label)

    _persist(sel, root, str(pool[sel.chosen_label].run_dir if sel.chosen_label in pool else ""))

    return sel


def trivial_selection(
    task_id: str,
    k: int,
    mode: str,
    candidates: dict[str, RunView],
    *,
    adapter: BenchmarkAdapter,
    work_dir: Path,
) -> Selection:
    """Deliver the single k=1 final without a selector session.

    The same non-answer drop rules apply, and the record is written with the standard
    selection.json schema at the path run_selection uses, so judge watchers find it once.
    """
    if len(candidates) > 1:
        raise ValueError(
            f"trivial_selection expects at most one candidate (the k=1 final), "
            f"got {sorted(candidates)}"
        )
    sel = Selection(task_id=task_id, mode=mode, k=k, trivial=True)
    pool, _artifacts = _render_pool(candidates, adapter, sel)
    sel.n_candidates = len(pool)
    if not pool:
        sel.error = "no candidate produced an artifact (finals-only pool has no base fallback)"
        return sel

    label = next(iter(pool))
    sel.ok = True
    sel.chosen_id = "A"
    sel.chosen_label = label
    sel.id_map = {"A": label}
    sel.chosen_run_id = pool[label].run_id
    sel.chosen_is_base = _is_base(label)
    sel.why = "trivial pool: single final at k=1; delivered without a selector session"

    root = Path(work_dir) / "selection" / f"{mode}{k}"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    _persist(sel, root, str(pool[label].run_dir))
    return sel


def reflect_adoption(
    task_id: str,
    sel: Selection,
    candidates: dict[str, RunView],
    *,
    adapter: BenchmarkAdapter,
    prop: Proposer,
    work_dir: Path,
    decisions: dict[str, str] | None = None,
) -> dict:
    """Learn from harness edits, decisions, trajectories, and deliverables after adoption."""
    chosen = candidates.get(sel.chosen_label)
    if not sel.ok or chosen is None:
        return {}
    root = Path(work_dir) / "reflection" / f"{sel.mode}{sel.k}"
    harnesses = Path(work_dir) / "harnesses"
    evidence = context.build(
        root,
        adapter,
        task_id,
        list(candidates.values()),
        harness_dir=harnesses / sel.chosen_label,
        pool=[
            (label, harnesses / label, rollout)
            for label, rollout in candidates.items()
            if not label.startswith("base")
        ],
        decisions=decisions,
    )
    shutil.copytree(adapter.base_harness(), root / "base_harness")
    (root / "adoption.json").write_text(
        json.dumps(
            {
                "chosen_label": sel.chosen_label,
                "candidate_ids": sel.id_map,
                "reason": sel.why,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    context.assert_no_leak_paths(root, adapter.spec.leak_globs)
    context.assert_no_grader_verdicts(root)
    experience = _distil(
        task_id, sel, candidates, adapter=adapter, prop=prop, root=root, layout=evidence.layout
    )
    sel.experience = experience
    _persist(sel, Path(work_dir) / "selection" / f"{sel.mode}{sel.k}", str(chosen.run_dir))
    return experience


def _distil(
    task_id: str,
    sel: Selection,
    pool: dict[str, RunView],
    *,
    adapter: BenchmarkAdapter,
    prop: Proposer,
    root: Path,
    bp: BenchPrompts | None = None,
    layout: str = "",
) -> dict:
    """Extract a supported lesson from the complete task-local improvement record."""
    mission = "\n\n".join(
        [
            P.experience(bp or resolve_prompts(adapter, task_id)),
            "# WHAT HAPPENED\n\n"
            f"Harness {sel.chosen_label} was adopted. The selection reasoning was:\n\n{sel.why}\n\n"
            "context/adoption.json maps any anonymous IDs in that reasoning to rollout labels. "
            "Compare context/harness/ with context/base_harness/ and the candidates in "
            "context/pool/. Read the decisions and execution contrasts before writing.\n\n"
            + layout,
        ]
    )
    exp_root = root.with_name(root.name + ".experience")
    shutil.rmtree(exp_root, ignore_errors=True)
    shutil.copytree(root, exp_root, ignore=shutil.ignore_patterns("selection.json"))
    context.assert_no_grader_verdicts(exp_root)
    try:
        reply = prop.call(mission, exp_root, f"experience.{sel.mode}{sel.k}")
    except ProposerError as exc:
        sel.usd += exc.cost_usd
        return {}
    sel.usd += reply.cost_usd
    edits = _EDITS.search(reply.text)
    if edits:
        (root / "edits.md").write_text(edits.group(1).strip() + "\n", encoding="utf-8")
    m = _EXP.search(reply.text)
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lower()
            if key in ("cue", "lesson", "evidence", "confidence"):
                out[key] = val.strip()
    if not all(out.get(key) for key in ("cue", "lesson", "evidence")):
        return {}
    if edits:
        out["edits"] = edits.group(1).strip()
    context.assert_tree_has_no_grader_verdicts({"EXPERIENCE.md": json.dumps(out)})
    return out
