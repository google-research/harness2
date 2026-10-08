#!/usr/bin/env python3
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

"""Optionally partition an installed benchmark, stratified by domain."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from harness2.benchmarks.tasks import discover


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bench", required=True, choices=("jb", "lab", "wb"))
    p.add_argument("--subset", choices=("office", "web", "code"))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--train-fraction", type=float, default=0.5)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    if (args.bench == "wb") != bool(args.subset):
        p.error("--subset is required for wb and only applies to wb")
    if not 0 < args.train_fraction < 1:
        p.error("--train-fraction must be between 0 and 1")
    try:
        by_domain = defaultdict(list)
        for task, domain in discover(args.bench, args.subset).items():
            by_domain[domain].append(task)
        train, test = [], []
        for domain, ids in sorted(by_domain.items()):
            random.Random(f"{args.seed}:{domain}").shuffle(ids)
            n = int(len(ids) * args.train_fraction)
            train.extend(ids[:n])
            test.extend(ids[n:])
        data = {
            "benchmark": args.bench + (f"-{args.subset}" if args.subset else ""),
            "seed": args.seed,
            "train_fraction": args.train_fraction,
            "train": sorted(train),
            "test": sorted(test),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    except (OSError, ValueError) as exc:
        p.exit(1, f"error: {exc}\n")
    print(f"{args.output}: {len(train)} train, {len(test)} test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
