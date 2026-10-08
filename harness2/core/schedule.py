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

"""Namespacing, the seeded stream order, and the drivers: one task, one domain, a sweep.

`ns()` is the only function that builds a run namespace; `Namespace` is the only thing that
globs over one. Scheme: <bench>-<substrate>-<mode><k>[-s<seed>][-<tag>].
"""

from __future__ import annotations

import json
import os
import random
import re
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from . import library as library_store
from . import recursion, runmark, selection
from . import tree as harness_tree
from .proposer import Proposer
from .spec import BenchmarkAdapter, RunView

MODES = ("agg", "seq")
MODE_NAMES = {"agg": "parallel", "seq": "sequential"}
_MODE_ALIASES = {"agg": "agg", "seq": "seq", "parallel": "agg", "sequential": "seq"}

ROLLOUT_PARALLEL = recursion.PHASE_WIDTH


def mode_token(name: str) -> str:
    """The on-disk token for a mode name: parallel/agg -> "agg", sequential/seq -> "seq"."""
    key = str(name or "").strip().lower()
    if key not in _MODE_ALIASES:
        raise ValueError(
            f"unknown mode {name!r}; expected parallel | sequential (or the on-disk tokens {MODES})"
        )
    return _MODE_ALIASES[key]


def validate_tag(tag: str) -> None:
    """Check a tag's shape: it becomes a path segment and must not spell a seed segment."""
    if not tag:
        return
    if not re.fullmatch(r"[A-Za-z0-9_.][A-Za-z0-9_.\-]*", tag):
        raise ValueError(
            f"tag {tag!r} must be [A-Za-z0-9_.-] with no leading '-' and "
            f"no '/': it becomes a path segment and a run-id segment"
        )
    if re.match(r"s\d+(-|$)", tag):
        raise ValueError(
            f"tag {tag!r} spells a seed segment; pass --seed instead so "
            f"seeded and tagged namespaces can never collide"
        )


def _modes_of(record: Mapping) -> dict:
    """The per-mode records of a summary / sweep record.

    The legacy key is still read so cached runs still resume.
    """
    if "modes" in record:
        return dict(record.get("modes") or {})
    return dict(record.get("arms") or {})


@dataclass(frozen=True)
class Namespace:
    """Every name for one (benchmark, substrate, mode, k, seed, tag) setting."""

    benchmark: str
    substrate: str
    mode: str
    k: int
    seed: int | None = None
    tag: str = ""

    def __str__(self) -> str:
        return ns(self.benchmark, self.substrate, self.mode, self.k, seed=self.seed, tag=self.tag)

    def run_id(self, task_id: str, label: str) -> str:
        return f"{self}/{task_id}/{label}"

    def task_dir(self, root: Path, task_id: str) -> Path:
        return Path(root) / str(self) / task_id.replace("/", "__")

    @classmethod
    def glob(cls, benchmark: str, substrate: str, k: int, *, mode: str = "*") -> tuple[str, str]:
        """The only glob over namespaces; both patterns match seeded, tagged and bare forms."""
        base = f"{benchmark}-{substrate}-{mode}{int(k)}"
        return base, f"{base}-*"


def ns(
    benchmark: str, substrate: str, mode: str, k: int, *, seed: int | None = None, tag: str = ""
) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; the method has exactly two: {MODES}")
    if not substrate:
        raise ValueError(
            "namespace needs a substrate name ('oc' | 'cx'); an empty one would "
            "let the two substrates' caches collide on every label"
        )
    validate_tag(tag)
    out = f"{benchmark}-{substrate}-{mode}{int(k)}"
    if seed is not None:
        out += f"-s{int(seed)}"
    if tag:
        out += f"-{tag}"
    return out


def stream_order(
    tasks_by_domain: Mapping[str, Sequence[str]],
    seed: int | None,
    reference_domain_order: Sequence[str],
) -> list[tuple[str, str]]:
    """The full (domain, task_id) stream, the one constructor of continual-learning order.

    seed=None is the reference order: `reference_domain_order`, tasks sorted. An int seed
    shuffles under Random(f"{seed}:__domains__") and Random(f"{seed}:{domain}").

    Each shuffle starts from the SORTED order, so the result depends ONLY on (seed, domain,
    task set) - never on dict iteration order, PYTHONHASHSEED, or which other domains are
    present: dropping one domain from a sweep leaves every other domain's stream identical.
    """
    domains = sorted(tasks_by_domain)
    if seed is None:
        missing = [d for d in domains if d not in set(reference_domain_order)]
        if missing:
            raise ValueError(
                f"reference_domain_order does not cover domain(s) {missing}; the frozen "
                f"order list is stale — regenerate it rather than letting an unlisted domain "
                f"land in an arbitrary position"
            )
        ordered_domains = [d for d in reference_domain_order if d in tasks_by_domain]
    else:
        ordered_domains = list(domains)
        random.Random(f"{seed}:__domains__").shuffle(ordered_domains)
    out: list[tuple[str, str]] = []
    for d in ordered_domains:
        tasks = sorted(tasks_by_domain[d])
        if seed is not None:
            random.Random(f"{seed}:{d}").shuffle(tasks)
        out.extend((d, t) for t in tasks)
    return out


def _default_reference_domain_order(by_domain: Mapping[str, Sequence[str]]) -> list[str]:
    """Descending task count, then name."""
    return sorted(by_domain, key=lambda d: (-len(by_domain[d]), d))


def _reference_domain_order(
    adapter: BenchmarkAdapter, by_domain: Mapping[str, Sequence[str]]
) -> list[str]:
    """The adapter's frozen domain order when it has one, else the count-then-name default."""
    fn = getattr(adapter, "reference_domain_order", None)
    if callable(fn):
        return [str(d) for d in fn()]
    return _default_reference_domain_order(by_domain)


def _selector_for(
    adapter: BenchmarkAdapter, prop: Proposer, selector_prop: Proposer | None
) -> Proposer:
    """Honor the selector channel while preserving fake/custom proposers when specs match."""
    if selector_prop is not None:
        return selector_prop
    spec = adapter.spec.channels["selector"]
    if prop.spec == spec:
        return prop
    audit = Path(prop.audit_dir) / "selector" if prop.audit_dir else None
    return Proposer(
        spec, audit_dir=audit, attempts=prop.attempts, project=prop.project, region=prop.region
    )


def _tree_is_base(surface, base_tree: harness_tree.Tree, tree: harness_tree.Tree) -> bool:
    chg = harness_tree.diff_summary(surface, base_tree, tree)
    return not (chg["added"] or chg["removed"] or chg["modified"])


def _restore_libraries(
    task_id: str,
    k: int,
    adapter: BenchmarkAdapter,
    runs_root: Path,
    modes: Sequence[str],
    seed: int | None,
    tag: str,
    libraries: dict[str, library_store.Library],
    selections: dict,
) -> None:
    """Replay a skipped task into its libraries from the saved chosen harness."""
    surface = adapter.substrate.surface()
    for mode in modes:
        lib = libraries.get(mode)
        sel = selections.get(mode) or {}
        label = str(sel.get("chosen_label") or "")
        if (
            not lib
            or not label
            or not sel.get("ok")
            or not library_store.has_experience(sel.get("experience"))
        ):
            continue
        n = Namespace(adapter.spec.name, adapter.substrate.name, mode, k, seed, tag)
        tree = harness_tree.read_tree(surface, n.task_dir(runs_root, task_id) / "harnesses" / label)
        if not tree:
            continue
        lib.add(
            task_id,
            tree,
            why=str(sel.get("why") or ""),
            label=label,
            run_id=str(sel.get("chosen_run_id") or ""),
            experience=sel.get("experience") or {},
        )


@dataclass
class TaskResult:
    task_id: str
    domain: str
    k: int
    seed: int | None = None
    status: str = "running"
    modes: dict = field(default_factory=dict)
    selections: dict = field(default_factory=dict)
    base: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "domain": self.domain,
            "k": self.k,
            "seed": self.seed,
            "status": self.status,
            "base": self.base,
            "modes": self.modes,
            "selections": self.selections,
            "errors": self.errors,
        }


def _merged_status(merged: dict) -> str:
    """ok iff every mode record on the merged summary succeeded and no error is recorded."""
    modes = merged.get("modes") or {}
    selections = merged.get("selections") or {}
    ok = (
        bool(modes)
        and not merged.get("errors")
        and all(
            (value or {}).get("ok")
            and (selections.get(mode) or {}).get("ok")
            and (selections.get(mode) or {}).get("chosen_label")
            for mode, value in modes.items()
        )
    )
    return "ok" if ok else "partial"


def _load_old(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_summary(summary_path: Path, res: TaskResult) -> None:
    """Merge `res` into the summary under an flock, atomically, deriving the merged status.

    Modes may be written by separate processes, so records accumulate per mode.
    """
    import fcntl

    lock = summary_path.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            merged = res.to_dict()
            if summary_path.is_file():
                old = _load_old(summary_path)
                for key in ("modes", "selections"):
                    prev = _modes_of(old) if key == "modes" else dict(old.get(key) or {})
                    prev.update(merged.get(key) or {})
                    merged[key] = prev

                merged["errors"] = list(merged.get("errors") or [])
                seen = {json.dumps(e, sort_keys=True, default=str) for e in merged["errors"]}
                for e in old.get("errors") or []:
                    if e == "no base rollout produced anything" and res.status in ("ok", "partial"):
                        continue
                    mode = str(e).partition(":")[0]
                    if (
                        (res.modes.get(mode) or {}).get("ok")
                        and (res.selections.get(mode) or {}).get("ok")
                        and (res.selections.get(mode) or {}).get("chosen_label")
                    ):
                        continue
                    if json.dumps(e, sort_keys=True, default=str) not in seen:
                        merged["errors"].append(e)
                if merged.get("status") in ("ok", "partial"):
                    merged["status"] = _merged_status(merged)
            tmp = summary_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(merged, indent=2, default=str))
            os.replace(tmp, summary_path)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _resolve_libraries(
    library: library_store.Library | None,
    libraries: dict[str, library_store.Library] | None,
    modes: Sequence[str],
) -> dict[str, library_store.Library]:
    mode_libs = dict(libraries or {})
    if library is None:
        return mode_libs
    if mode_libs:
        raise ValueError("pass either library or libraries, not both")
    if len(modes) != 1:
        raise ValueError(
            "a shared mutable library would leak one mode into another; "
            "pass libraries={'agg': ..., 'seq': ...} instead"
        )
    mode_libs[modes[0]] = library
    return mode_libs


def _resume(
    summary_path: Path,
    task_id: str,
    k: int,
    adapter: BenchmarkAdapter,
    runs_root: Path,
    modes: Sequence[str],
    seed: int | None,
    tag: str,
    mode_libs: dict[str, library_store.Library],
    res: TaskResult,
) -> bool:
    """Fill `res` from a cached summary covering every requested mode; True if it did."""
    try:
        got = json.loads(summary_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"  [{task_id}] unreadable summary.json ({type(exc).__name__}: {exc}); re-running",
            flush=True,
        )
        return False
    if got.get("status") != "ok":
        return False
    cached_modes = _modes_of(got)
    cached_sels = got.get("selections") or {}
    missing = sorted(
        m
        for m in modes
        if not (
            (cached_modes.get(m) or {}).get("ok")
            and (cached_sels.get(m) or {}).get("ok")
            and (cached_sels.get(m) or {}).get("chosen_label")
        )
    )
    if missing:
        print(
            f"  [{task_id}] cached summary covers {sorted(cached_modes)}, re-running for {missing}",
            flush=True,
        )
        return False
    for mode in modes:
        sel = cached_sels[mode]
        namespace = Namespace(adapter.spec.name, adapter.substrate.name, mode, k, seed, tag)
        harness_dir = namespace.task_dir(runs_root, task_id) / "harnesses" / sel["chosen_label"]
        run_dir = Path(sel.get("chosen_run_dir") or "")
        try:
            if not sel.get("chosen_run_dir") or not run_dir.is_dir():
                raise ValueError("adopted run directory is missing")
            if sel.get("evidence") == "trajectory" and adapter.spec.state_deliverable:
                artifact = adapter.trajectory(run_dir, obs_trunc=adapter.spec.obs_trunc)
            else:
                artifact = adapter.deliverable_text(run_dir) if adapter.delivered(run_dir) else ""
            if not artifact.strip() or artifact.startswith("(no trajectory"):
                raise ValueError("adopted output is missing")
            artifacts_intact = getattr(adapter, "artifacts_intact", None)
            if callable(artifacts_intact) and not artifacts_intact(run_dir):
                raise ValueError("adopted execution artifacts have changed")
            if not harness_tree.is_tree(adapter.substrate.surface(), harness_dir):
                raise ValueError("adopted harness is missing")
            fingerprint = sel.get("harness_fingerprint")
            if fingerprint and runmark.fingerprint(harness_dir) != fingerprint:
                raise ValueError("adopted harness has changed")
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"  [{task_id}] cannot resume {mode}: {exc}; re-running", flush=True)
            return False
    res.status = "skipped"
    res.base = got.get("base", [])
    res.modes = cached_modes
    res.selections = cached_sels
    res.errors = got.get("errors", [])
    _restore_libraries(task_id, k, adapter, runs_root, modes, seed, tag, mode_libs, res.selections)
    return True


def _dead(label: str, rid: str, dead_dir: Path, error: str) -> RunView:
    return RunView(
        label=label, run_id=rid, run_dir=dead_dir, ok=False, delivered=False, error=error
    )


def _roll_bases(
    task_id: str, adapter: BenchmarkAdapter, pending: Sequence[tuple[str, str]], dead_dir: Path
) -> dict[str, RunView]:
    """Roll the pending (label, run_id) reference executions, natively batched when possible."""
    if not pending:
        return {}
    rolled: dict[str, RunView] = {}
    execute_many = getattr(adapter, "execute_many", None)
    if len(pending) > 1 and callable(execute_many):
        try:
            dirs = list(execute_many(task_id, None, [rid for _lbl, rid in pending]))
            for i, (label, rid) in enumerate(pending):
                if i >= len(dirs):
                    rolled[label] = _dead(
                        label, rid, dead_dir, "batch adapter returned too few run directories"
                    )
                    continue
                rd = Path(dirs[i])
                exists = rd.exists()
                rolled[label] = RunView(
                    label=label,
                    run_id=rid,
                    run_dir=rd,
                    ok=exists,
                    delivered=exists and adapter.delivered(rd),
                    error="" if exists else "adapter returned a missing run directory",
                )
        except Exception as exc:
            for label, rid in pending:
                rolled[label] = _dead(label, rid, dead_dir, f"{type(exc).__name__}: {exc}")
        return rolled

    def _one(label_rid: tuple[str, str]) -> RunView:
        label, rid = label_rid
        try:
            rd = adapter.execute(task_id, None, rid)
            return RunView(
                label=label, run_id=rid, run_dir=rd, ok=True, delivered=adapter.delivered(rd)
            )
        except Exception as exc:
            return _dead(label, rid, dead_dir, f"{type(exc).__name__}: {exc}")

    if len(pending) == 1:
        rv = _one(pending[0])
        return {rv.label: rv}
    width = min(len(pending), ROLLOUT_PARALLEL)
    with ThreadPoolExecutor(max_workers=max(1, width)) as pool_r:
        return {rv.label: rv for rv in pool_r.map(_one, pending)}


def _run_one_mode(
    mode: str,
    task_id: str,
    k: int,
    base: list[RunView],
    *,
    adapter: BenchmarkAdapter,
    prop: Proposer,
    selector: Proposer,
    n: Namespace,
    runs_root: Path,
    mode_lib: library_store.Library | None,
    base_tree: harness_tree.Tree,
    surface,
    pool_policy: str,
    res: TaskResult,
) -> None:
    """Run one recursion mode over the shared bases, select a final, and record it on `res`."""
    work_dir = n.task_dir(runs_root, task_id)
    lib_dir = mode_lib.context_dir() if mode_lib else None
    r = recursion.run_mode(
        mode,
        task_id,
        k,
        base,
        adapter=adapter,
        prop=prop,
        work_dir=work_dir,
        run_id_of=lambda lbl, _n=n: _n.run_id(task_id, lbl),
        base_tree=base_tree,
        library_dir=lib_dir,
    )
    res.modes[mode] = r.to_dict()
    candidates = selection.selection_pool(mode, k, r.rollouts, base)
    if pool_policy == "finals" and k == 1:
        sel = selection.trivial_selection(
            task_id, k, mode, candidates, adapter=adapter, work_dir=work_dir
        )
    else:
        sel = selection.run_selection(
            task_id,
            k,
            mode,
            candidates,
            adapter=adapter,
            prop=selector,
            work_dir=work_dir,
            pool_kind="finals",
        )
    if mode_lib and sel.ok:
        selection.reflect_adoption(
            task_id,
            sel,
            r.rollouts,
            adapter=adapter,
            prop=selector,
            work_dir=work_dir,
            decisions={stage.label: stage.notes or stage.error for stage in r.stages},
        )
    d = sel.to_dict()
    d["chosen_run_dir"] = str(
        candidates[sel.chosen_label].run_dir if sel.chosen_label in candidates else ""
    )

    chosen_tree = r.trees.get(sel.chosen_label, base_tree)
    chosen_tree_is_base = _tree_is_base(surface, base_tree, chosen_tree)
    d["chosen_tree_is_base"] = chosen_tree_is_base
    if sel.chosen_label:
        d["harness_fingerprint"] = runmark.fingerprint(work_dir / "harnesses" / sel.chosen_label)
    res.selections[mode] = d
    if not sel.ok:
        res.errors.append(f"{mode}: selection failed: {sel.error}")
    chosen = candidates.get(sel.chosen_label)
    if sel.ok and chosen is not None and not chosen.delivered:
        res.errors.append(f"{mode}: selected run {chosen.run_id} produced no deliverable")
    if mode_lib and sel.ok and library_store.has_experience(sel.experience):
        mode_lib.add(
            task_id,
            chosen_tree,
            why=sel.why,
            label=sel.chosen_label,
            run_id=sel.chosen_run_id,
            experience=sel.experience,
        )


def run_task(
    task_id: str,
    k: int,
    *,
    adapter: BenchmarkAdapter,
    prop: Proposer,
    runs_root: Path,
    modes: Sequence[str] = MODES,
    seed: int | None = None,
    tag: str = "",
    library: library_store.Library | None = None,
    libraries: dict[str, library_store.Library] | None = None,
    selector_prop: Proposer | None = None,
    pool_policy: str = "finals",
) -> TaskResult:
    """k shared reference executions -> every mode -> blind selection per mode -> summary.json.

    `seed` enters every namespace and the summary metadata; ordering tasks by it is the
    caller's job. `pool_policy` is "finals" -- exactly the k finals per mode, the published
    convention and the only pool this release builds; it is a parameter rather than a literal
    only so the cost claim at k=1 (no selector session) stays legible at the call site.
    """
    if int(k) < 1:
        raise ValueError(f"k must be positive, got {k!r}")
    if not modes or len(set(modes)) != len(modes):
        raise ValueError("modes must contain at least one distinct recursion mode")
    unknown = sorted(set(modes) - set(MODES))
    if unknown:
        raise ValueError(f"unknown modes {unknown}; expected a subset of {MODES}")
    if pool_policy != "finals":
        raise ValueError(
            f"unknown pool_policy {pool_policy!r}; this release builds exactly "
            f"one selection pool, 'finals' (the k composed finals)"
        )
    mode_libs = _resolve_libraries(library, libraries, modes)
    selector = _selector_for(adapter, prop, selector_prop)
    bench, sub = adapter.spec.name, adapter.substrate.name

    domain = adapter.domain_of(task_id)
    res = TaskResult(task_id=task_id, domain=domain, k=k, seed=seed)
    base_ns = Namespace(bench, sub, "agg", k, seed, tag)
    work = base_ns.task_dir(runs_root, task_id)
    work.mkdir(parents=True, exist_ok=True)
    summary_path = work / "summary.json"

    if summary_path.is_file() and _resume(
        summary_path, task_id, k, adapter, runs_root, modes, seed, tag, mode_libs, res
    ):
        return res

    surface = adapter.substrate.surface()
    base_tree = harness_tree.read_tree(surface, adapter.base_harness())

    labels = [f"base{i}" for i in range(1, k + 1)]
    run_ids = [base_ns.run_id(task_id, label) for label in labels]

    dead_dir = work / "missing"

    pending = list(zip(labels, run_ids))
    rolled = _roll_bases(task_id, adapter, pending, dead_dir)
    base: list[RunView] = [rolled[label] for label in labels]

    res.base = [{"label": b.label, "run_id": b.run_id, "run_dir": str(b.run_dir)} for b in base]
    if not any(b.ok for b in base):
        res.status = "base-failed"
        res.errors.append("no base rollout produced anything")
        _write_summary(summary_path, res)
        return res

    for mode in modes:
        n = Namespace(bench, sub, mode, k, seed, tag)
        try:
            _run_one_mode(
                mode,
                task_id,
                k,
                base,
                adapter=adapter,
                prop=prop,
                selector=selector,
                n=n,
                runs_root=runs_root,
                mode_lib=mode_libs.get(mode),
                base_tree=base_tree,
                surface=surface,
                pool_policy=pool_policy,
                res=res,
            )
        except Exception as exc:
            res.modes[mode] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-1500:],
            }
            res.errors.append(f"{mode}: {type(exc).__name__}: {exc}")
        res.status = "partial"
        _write_summary(summary_path, res)

    res.status = _merged_status(res.to_dict())
    _write_summary(summary_path, res)
    return res


def run_domain(
    domain: str,
    task_ids: Sequence[str],
    k: int,
    *,
    adapter: BenchmarkAdapter,
    prop: Proposer,
    runs_root: Path,
    library_root: Path,
    modes: Sequence[str] = MODES,
    seed: int | None = None,
    tag: str = "",
    selector_prop: Proposer | None = None,
    pool_policy: str = "finals",
) -> dict:
    """Every task in one domain, in `stream_order()` order, threading the library forward."""
    surface = adapter.substrate.surface()
    libs = {
        mode: library_store.Library(root=Path(library_root) / mode, domain=domain, surface=surface)
        for mode in modes
    }
    out = {
        "domain": domain,
        "n_tasks": len(task_ids),
        "seed": seed,
        "done": 0,
        "failed": 0,
        "tasks": [],
    }
    for i, tid in enumerate(task_ids, 1):
        try:
            for lib in libs.values():
                lib.retain(task_ids[: i - 1])
            before = {mode: len(lib.entries()) for mode, lib in libs.items()}
            r = run_task(
                tid,
                k,
                adapter=adapter,
                prop=prop,
                runs_root=runs_root,
                modes=modes,
                seed=seed,
                tag=tag,
                libraries=libs,
                selector_prop=selector_prop,
                pool_policy=pool_policy,
            )
            if r.status in ("ok", "skipped"):
                out["done"] += 1
                row = {"task": tid, "status": r.status, "library_entries_before": before}
            else:
                out["failed"] += 1
                row = {
                    "task": tid,
                    "status": r.status,
                    "error": "; ".join(r.errors) if r.errors else r.status,
                    "library_entries_before": before,
                }
        except Exception as exc:
            out["failed"] += 1
            row = {"task": tid, "status": "exception", "error": f"{type(exc).__name__}: {exc}"}
        out["tasks"].append(row)
        err = f"  :: {row['error']}" if row.get("error") else ""
        print(f"  [{domain}] {i}/{len(task_ids)} {row['status']:<12} {tid}{err}", flush=True)
    return out


def task_stream(
    adapter: BenchmarkAdapter,
    *,
    split: str = "all",
    seed: int | None = None,
    only_domains: Sequence[str] = (),
    only_tasks: Sequence[str] = (),
    max_tasks: int | None = None,
) -> dict[str, list[str]]:
    """Resolve the ordered task stream for execution, previews, and resume identity."""
    by_domain: dict[str, list[str]] = defaultdict(list)
    for t in adapter.tasks(split):
        by_domain[adapter.domain_of(t)].append(t)
    if only_domains:
        keep = set(only_domains)
        missing = sorted(keep - set(by_domain))
        if missing:
            raise ValueError(
                f"only_domains names unknown domain(s) {missing}; split "
                f"{split!r} has {sorted(by_domain)}"
            )
        by_domain = {d: v for d, v in by_domain.items() if d in keep}
    if only_tasks:
        wanted = set(only_tasks)
        missing_t = sorted(wanted - {t for v in by_domain.values() for t in v})
        if missing_t:
            raise ValueError(
                f"only_tasks names unknown task id(s) {missing_t} for split "
                f"{split!r}" + (f" / domains {sorted(by_domain)}" if only_domains else "")
            )
    if max_tasks is not None and int(max_tasks) < 1:
        raise ValueError(f"max_tasks must be positive, got {max_tasks!r}")

    stream = stream_order(by_domain, seed, _reference_domain_order(adapter, by_domain))
    ordered_tasks: dict[str, list[str]] = defaultdict(list)
    for d, t in stream:
        if only_tasks and t not in set(only_tasks):
            continue
        if max_tasks is not None and len(ordered_tasks[d]) >= int(max_tasks):
            continue
        ordered_tasks[d].append(t)
    return dict(ordered_tasks)


def run_sweep(
    k: int,
    *,
    adapter: BenchmarkAdapter,
    prop: Proposer,
    runs_root: Path,
    library_root: Path,
    split: str = "all",
    modes: Sequence[str] = MODES,
    seed: int | None = None,
    tag: str = "",
    domain_parallel: int = 1,
    only_domains: Sequence[str] = (),
    selector_prop: Proposer | None = None,
    pool_policy: str = "finals",
    max_tasks: int | None = None,
    only_tasks: Sequence[str] = (),
) -> dict:
    """Run domains concurrently, preserving task order after stream filters are applied."""
    ordered_tasks = task_stream(
        adapter,
        split=split,
        seed=seed,
        only_domains=only_domains,
        only_tasks=only_tasks,
        max_tasks=max_tasks,
    )
    order = list(ordered_tasks)

    started = datetime.now(timezone.utc).isoformat()
    rows = []
    selector = _selector_for(adapter, prop, selector_prop)
    with ThreadPoolExecutor(max_workers=domain_parallel) as pool:
        futs = {
            pool.submit(
                run_domain,
                d,
                ordered_tasks[d],
                k,
                adapter=adapter,
                prop=prop,
                runs_root=runs_root,
                library_root=library_root,
                modes=modes,
                seed=seed,
                tag=tag,
                selector_prop=selector,
                pool_policy=pool_policy,
            ): d
            for d in order
        }
        for f in as_completed(futs):
            try:
                rows.append(f.result())
            except Exception as exc:
                rows.append({"domain": futs[f], "error": f"{type(exc).__name__}: {exc}"})
    return {
        "benchmark": adapter.spec.name,
        "substrate": adapter.substrate.name,
        "k": k,
        "modes": list(modes),
        "seed": seed,
        "tag": tag,
        "domain_order": order,
        "max_tasks": max_tasks,
        "only_tasks": list(only_tasks),
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "n_domains": len(order),
        "domains": rows,
    }
