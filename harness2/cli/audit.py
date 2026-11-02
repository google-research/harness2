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

"""Audit a finished sweep for answer-key text — read-only, offline, no model calls.

For each task: did its own grading text appear in an execution, in a context a model read, or
in an adopted harness, and did a later task in the same domain read that task's library entry?
Also lists URLs on the hosts docs/EXPERIMENTS.md#web-access says to block. Writes
answer_key_audit.json next to the task summaries, which harness2-report then warns from. Exits
1 when any task carries or inherits a key.

    python3 -m harness2.cli.audit --bench jb --substrate oc --k 1 --mode parallel,sequential
"""

from __future__ import annotations

import argparse
import sys

from ..core import audit as A
from . import _common as C


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="harness2.cli.audit", description=__doc__, formatter_class=C.HelpFormatter
    )
    C.add_cell_args(p.add_argument_group("cell"))
    g = p.add_argument_group("run identity (names the sweeps)")
    g.add_argument("--k", default="1", help="comma list of scales to audit, e.g. 1,2,3")
    g.add_argument(
        "--seed",
        type=int,
        default=None,
        help="stream-order seed of the sweep; omit for the seedless namespace",
    )
    C.add_modes_arg(g)
    args = p.parse_args(argv)
    try:
        modes = C.parse_modes(args.modes)
        ks = sorted({int(s) for s in args.k.split(",") if s.strip()})
    except ValueError as exc:
        raise SystemExit(f"harness2-audit: {exc}")
    if not ks:
        raise SystemExit("--k named no scale")

    adapter = C.make_adapter(args.bench, args.subset, args.substrate)
    runs_root = C.runs_root_for(adapter)
    found = False
    flagged = False
    for k in ks:
        summary_ns = C.summary_namespace(adapter, k, seed=args.seed)
        audits = A.audit_namespace(adapter, runs_root, summary_ns, k=k, modes=modes, seed=args.seed)
        if not audits:
            print(f"  ! no task summaries under {runs_root / summary_ns}", flush=True)
            continue
        found = True
        flagged |= any(a.verdict in ("contaminated", "inherits") for a in audits)
        out = A.write(audits, runs_root / summary_ns / A.FILENAME)
        print(A.render(audits, title=f"{summary_ns} modes={','.join(map(C.mode_label, modes))}"))
        print(f"  wrote {out}\n", flush=True)
    if not found:
        raise SystemExit("nothing to audit")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
