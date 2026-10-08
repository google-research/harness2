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

"""Run one adaptation sweep: every task of a split, domains parallel, tasks in stream order.

Thin argparse over core.schedule.run_sweep. Costs money when run for real: each task is
R=4k task-executor rollouts and P=3k improver sessions. `--dry-run` prints the exact
(domain, task) stream and namespaces without creating a Proposer, touching a substrate or
validating a model setting.

A real run checks the judge before the first rollout, because the namespace records the
judge model and `harness2-judge` refuses to grade it under a different one: discovering a
missing judge after the sweep means paying for the sweep twice. The check is offline — a
judge model must be named and a benchmark that grades in its own environment must have it
installed — so it catches an unset or unbuilt judge, not one an account cannot serve.

    python3 -m harness2.cli.run_sweep --bench jb --substrate oc --k 2 --split all
    python3 -m harness2.cli.run_sweep --bench wb --subset web --substrate cx \\
        --k 1 --split all --seed 7 --modes parallel --max-tasks 2 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..core import schedule
from ..core.proposer import Proposer
from . import _common as C


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harness2.cli.run_sweep", description=__doc__, formatter_class=C.HelpFormatter
    )
    C.add_cell_args(p.add_argument_group("cell"))
    C.add_run_identity_args(p.add_argument_group("run identity (enters the namespace)"))
    g = p.add_argument_group("run control")
    g.add_argument("--split", choices=("all", "train", "test"), default="all")
    g.add_argument("--split-file", type=Path, help="optional partition from scripts/split_tasks.py")
    g.add_argument("--workers", type=int, default=1, help="domains to run concurrently")
    g.add_argument(
        "--only-domains", default="", help="comma list restricting the sweep to these domains"
    )
    g.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="per-domain cap on the stream (the first N tasks of every domain); "
        "drop it for the full run",
    )
    g.add_argument(
        "--dry-run",
        action="store_true",
        help="print the stream order, the namespaces and the split's domains, then "
        "exit — no rollouts, no sessions, nothing written",
    )
    return p


def _csv(raw: str) -> tuple[str, ...]:
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def _stream_for(adapter, args) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    by_domain = schedule.task_stream(
        adapter,
        split=args.split,
        seed=args.seed,
        only_domains=_csv(args.only_domains),
        max_tasks=args.max_tasks,
    )
    return by_domain, [(domain, task) for domain, tasks in by_domain.items() for task in tasks]


def _print_stream(adapter, args, modes) -> int:
    by_domain, stream = _stream_for(adapter, args)
    print(
        f"# {adapter.spec.name}-{adapter.substrate.name} split={args.split} "
        f"seed={args.seed} k={args.k} modes={','.join(C.mode_label(m) for m in modes)}"
    )
    for m in modes:
        namespace = schedule.ns(
            adapter.spec.name, adapter.substrate.name, m, args.k, seed=args.seed
        )
        print(f"#   namespace[{C.mode_label(m)}]: {namespace}")
    print(
        f"#   summaries under: {C.runs_root_for(adapter)}/"
        f"{C.summary_namespace(adapter, args.k, seed=args.seed)}"
    )
    print(f"#   library root:    {C.library_root_for(adapter, args.k, seed=args.seed)}")
    print(f"#   domains parallel: {args.workers}")
    for i, (domain, task) in enumerate(stream, 1):
        print(f"{i:>4}  {domain:<40} {task}")
    print("# domains of this split, with task counts (the legal --only-domains names):")
    for d, tasks in by_domain.items():
        print(f"{len(tasks):>4}  {d}")
    cap = f", max {args.max_tasks} per domain" if args.max_tasks is not None else ""
    print(
        f"# {len(stream)} tasks over {len(by_domain)} domains "
        f"(domains parallel, tasks within a domain sequential{cap})"
    )
    return 0 if stream else 1


def main(argv: list[str] | None = None) -> int:
    """Parse, then run `_main`, printing a misconfiguration as one remediation line."""
    args = _build_parser().parse_args(argv)
    try:
        return _main(args)
    except (config.ConfigError, RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _main(args: argparse.Namespace) -> int:
    if args.k < 1 or args.workers < 1:
        raise ValueError("--k and --workers must be positive")
    modes = C.parse_modes(args.modes)

    if args.dry_run:
        return _print_stream(
            C.make_adapter(args.bench, args.subset, args.substrate, args.split_file), args, modes
        )

    adapter = C.make_adapter(args.bench, args.subset, args.substrate, args.split_file)
    _stream_for(adapter, args)
    from ..preflight import require_ready

    require_ready(adapter, judge=True)

    runs_root = C.runs_root_for(adapter)
    library_root = C.library_root_for(adapter, args.k, seed=args.seed)
    summary_ns = C.summary_namespace(adapter, args.k, seed=args.seed)
    _check_identity(adapter, args, runs_root / summary_ns)
    prop = Proposer(adapter.spec.channels["proposer"], audit_dir=runs_root / "_audit" / summary_ns)

    only = _csv(args.only_domains)
    lanes = args.workers
    print(
        f"sweep {summary_ns}: {lanes} domain(s) in parallel, tasks within a domain in stream order",
        flush=True,
    )
    rep = schedule.run_sweep(
        args.k,
        adapter=adapter,
        prop=prop,
        runs_root=runs_root,
        library_root=library_root,
        split=args.split,
        modes=modes,
        seed=args.seed,
        tag="",
        domain_parallel=lanes,
        only_domains=only,
        max_tasks=args.max_tasks,
        pool_policy="finals",
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rec = runs_root / "_sweeps" / f"{summary_ns}-{stamp}.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")

    done = sum(int(d.get("done", 0)) for d in rep["domains"])
    failed = sum(int(d.get("failed", 0)) for d in rep["domains"])
    dead = [d["domain"] for d in rep["domains"] if d.get("error")]
    if not rep["domains"]:
        print(
            f"\nsweep {summary_ns}: zero tasks scheduled (split={args.split!r}, "
            f"only_domains={only!r}, max_tasks={args.max_tasks!r}); "
            f"refusing to report success"
        )
        print(f"record: {rec}")
        return 1
    print(
        f"\nsweep {summary_ns}: {done} tasks ok, {failed} failed, "
        f"{len(dead)} domain(s) died{': ' + ', '.join(dead) if dead else ''}"
    )
    print(f"record: {rec}")
    print(
        f"next:   python3 -m harness2.cli.judge --bench {args.bench}"
        + (f" --subset {args.subset}" if args.subset else "")
        + f" --substrate {args.substrate} --k {args.k}"
        + (f" --seed {args.seed}" if args.seed is not None else "")
        + f" --modes {args.modes}"
    )

    if dead:
        return 1
    return 2 if failed else 0


def _check_identity(adapter, args, namespace_dir: Path) -> None:
    """Prevent a resumed run or library from silently mixing models or task partitions."""
    identity = {
        "method_version": config.METHOD_VERSION,
        "executor": config.SOLVER_MODEL_OC if args.substrate == "oc" else config.SOLVER_MODEL_CX,
        "improver": config.IMPROVER_MODEL,
        "judge": config.JUDGE_MODEL,
        "project": config.VERTEX_PROJECT,
        "location": config.VERTEX_REGION,
        "judge_endpoint": config.JB_JUDGE_API_BASE,
        "endpoint": config.CODEX_BASE_URL if args.substrate == "cx" else "",
        "split": args.split,
        "tasks": _stream_for(adapter, args)[1],
        "max_tasks": args.max_tasks,
        "domains": sorted(_csv(args.only_domains)),
        "seed": args.seed,
    }
    namespace_dir.mkdir(parents=True, exist_ok=True)
    path = namespace_dir / "config.json"
    if path.is_file():
        if json.loads(path.read_text()) != json.loads(json.dumps(identity)):
            raise ValueError(
                "This run directory belongs to different models or tasks. "
                "Choose a new HARNESS2_RUNS_DIR for a new experiment."
            )
    else:
        path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
