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

"""Run the real recursion and library workflow with local, deterministic editors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import config
from ..core.proposer import Proposer
from ..core.schedule import mode_token, run_domain
from ..demo import DemoAdapter, DemoProposer


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runs/demo"))
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--mode", choices=("parallel", "sequential", "both"), default="both")
    parser.add_argument(
        "--substrate", choices=("oc", "cx"), default="oc",
        help="oc: deterministic offline demo; cx: real native Codex sessions (uses model quota)",
    )
    parser.add_argument("--max-tasks", type=int, choices=(1, 2), default=2)
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be positive")
    root = args.output.expanduser().resolve()
    record = root / "codex-config.json"
    if args.substrate == "oc" and record.exists():
        parser.error("this is a Codex demo output directory; use a new --output for the offline demo")
    if args.substrate == "cx":
        if config.CODEX_AUTH_MODE != "chatgpt" or not config.SOLVER_MODEL_CX:
            parser.error("the Codex demo needs HARNESS2_CODEX_AUTH_MODE=chatgpt and HARNESS2_CODEX_MODEL")
        root.mkdir(parents=True, exist_ok=True)
        identity = {
            "executor": config.SOLVER_MODEL_CX,
            "improver": config.IMPROVER_MODEL or config.SOLVER_MODEL_CX,
            "auth_mode": config.CODEX_AUTH_MODE,
            "k": args.k,
            "mode": args.mode,
            "max_tasks": args.max_tasks,
        }
        if not record.exists() and (root / "base").exists():
            parser.error("this is an offline demo output directory; use a new --output for Codex")
        if record.is_file() and json.loads(record.read_text()) != identity:
            parser.error("this output directory belongs to another Codex demo; use a new --output")
        record.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    adapter = DemoAdapter(root, substrate=args.substrate)
    if args.substrate == "cx":
        adapter.substrate.preflight()
        proposer = Proposer(adapter.spec.channels["proposer"], audit_dir=root / "audit")
        print("Real Codex demo: uses saved ChatGPT login and subscription quota.", flush=True)
    else:
        proposer = DemoProposer(adapter.spec.channels["proposer"])
    modes = ("agg", "seq") if args.mode == "both" else (mode_token(args.mode),)
    result = run_domain(
        "accounting",
        adapter.tasks()[:args.max_tasks],
        args.k,
        adapter=adapter,
        prop=proposer,
        runs_root=adapter.spec.runs_root,
        library_root=root / "library" / f"k{args.k}",
        modes=modes,
    )
    result["execution_count"] = adapter.execution_count
    result["editor_calls"] = len(proposer._calls)
    report = root / "demo.json"
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Local demo: {result['done']} tasks completed; {result['failed']} failed. {report}")
    return int(result["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
