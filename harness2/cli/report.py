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

"""Render the score report for one cell from cached judged.json rows — read-only.

The figure each benchmark reports per (mode, k) — partial credit and all-criteria pass on LAB,
one score elsewhere — with pick provenance, the k=1 noise-floor note and the sanity warnings;
fell-back counts and improver spend come from the task summaries.

    python3 -m harness2.cli.report --bench jb --substrate oc --k 1,2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..core import report as R
from ..core import schedule as SCHED
from . import _common as C
from .judge import _load_rows


def _spend_and_fallbacks(ns_dir: Path, modes: tuple[str, ...], k: int) -> tuple[dict, dict]:
    """(fell_back, usd) keyed (mode token, k), from the summaries the sweep checkpointed."""
    fell_back: dict[tuple[str, int], int] = {}
    usd: dict[tuple[str, int], float] = {}
    for summary_path in sorted(ns_dir.glob("*/summary.json")):
        try:
            summary = json.loads(summary_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! skipping unreadable {summary_path}: {exc}", flush=True)
            continue
        records = SCHED._modes_of(summary)
        for mode in modes:
            key = (mode, k)
            rec = (records or {}).get(mode) or {}
            fell_back[key] = fell_back.get(key, 0) + len(rec.get("fell_back") or [])
            spend = sum(float(s.get("usd") or 0.0) for s in rec.get("stages") or [])
            sel = (summary.get("selections") or {}).get(mode) or {}
            usd[key] = usd.get(key, 0.0) + spend + float(sel.get("usd") or 0.0)
    return fell_back, usd


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="harness2.cli.report", description=__doc__, formatter_class=C.HelpFormatter
    )
    C.add_cell_args(p.add_argument_group("cell"))
    g = p.add_argument_group("run identity (names the sweeps)")
    g.add_argument("--k", default="1", help="comma list of scales to report, e.g. 1,2,3")
    g.add_argument(
        "--seed",
        type=int,
        default=None,
        help="stream-order seed of the sweep; omit for the seedless namespace",
    )
    C.add_modes_arg(g)
    args = p.parse_args(argv)
    modes = C.parse_modes(args.modes)
    try:
        ks = sorted({int(s) for s in args.k.split(",") if s.strip()})
    except ValueError:
        raise SystemExit(f"--k takes a comma list of ints, got {args.k!r}")
    if not ks:
        raise SystemExit("--k named no scale")

    adapter = C.make_adapter(args.bench, args.subset, args.substrate)
    rows, fell_back, usd = [], {}, {}
    for k in ks:
        ns_dir = C.runs_root_for(adapter) / C.summary_namespace(adapter, k, seed=args.seed)
        judged = ns_dir / "judged.json"
        got = _load_rows(judged)
        if not got:
            print(f"  ! no judged rows at {judged} — run cli.judge for k={k} first", flush=True)
            continue
        rows.extend(r for r in got if r.mode in modes)
        fb, sp = _spend_and_fallbacks(ns_dir, modes, k)
        fell_back.update(fb)
        usd.update(sp)
    if not rows:
        raise SystemExit("nothing to report")

    title = (
        f"{adapter.spec.name}-{adapter.substrate.name}"
        + (f" seed={args.seed}" if args.seed is not None else "")
        + f" modes={','.join(C.mode_label(m) for m in modes)}"
    )
    summaries = R.summarise(rows, fell_back=fell_back, usd=usd)
    print(R.render(summaries, title=title, bench=adapter.spec.name))
    spend = sum(s.usd for s in summaries)
    if spend:
        print(
            f"\n  improver (editors, composer, selection, reflection) spend across reported modes: "
            f"${spend:.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
