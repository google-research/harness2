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

"""Real DeepSeek SDK + deterministic local provider; judging is a separate command."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from ..core import judge, report, schedule, tree
from ..deepseek_demo import FIXTURE_KEY, DeepSeekDemoAdapter, MessagesFixture
from ..demo import DemoProposer
from ..substrate.deepseek import MANIFEST, DeepSeekSettings, DeepSeekSubstrate, write_json


def evaluate(adapter: DeepSeekDemoAdapter, k: int, modes: tuple[str, ...]) -> dict:
    namespace = adapter.spec.runs_root / f"deepseek-demo-ds-agg{k}"
    summaries = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(namespace.glob("*/summary.json"))
    ]
    if not summaries:
        raise ValueError(f"No completed experiment at {namespace}; run the demo first")
    bases = [
        {
            "task_id": summary["task_id"],
            "run_id": base["run_id"],
            "score": adapter.score(Path(base["run_dir"]), summary["task_id"]),
        }
        for summary in summaries
        for base in summary["base"]
    ]
    selections = [
        summary["selections"][mode]
        for summary in summaries
        for mode in modes
        if mode in summary["selections"]
    ]
    rows = judge.judge_selections(adapter, selections)
    payload = {
        "baseline": bases,
        "adapted": [{**asdict(row), "graded": row.graded, "frac": row.frac} for row in rows],
        "claim": "Measured on a scripted local provider unless --live was explicitly used; "
        "this demonstrates mechanics, not model-quality improvement.",
    }
    write_json(adapter.root / "evaluation.json", payload)
    print(report.render(report.summarise(rows), title="DeepSeek demo: separate evaluation"))
    passed = sum(row["score"]["n_passed"] for row in bases if row["score"] is not None)
    graded = sum(row["score"] is not None for row in bases)
    print(f"Baseline: {passed}/{graded} exact answers; {len(bases) - graded} ungraded.")
    return payload


def _run(adapter: DeepSeekDemoAdapter, k: int, modes: tuple[str, ...]) -> dict:
    proposer = DemoProposer(adapter.spec.channels["proposer"])
    result = schedule.run_domain(
        "accounting",
        adapter.tasks(),
        k,
        adapter=adapter,
        prop=proposer,
        runs_root=adapter.spec.runs_root,
        library_root=adapter.root / "library" / f"k{k}",
        modes=modes,
    )
    result["editor_calls"] = len(proposer._calls)
    result["executor_invocations"] = adapter.execution_count
    states: dict[str, int] = {}
    for mode in set(modes) | {"agg"}:
        namespace = adapter.root / "executions" / f"deepseek-demo-ds-{mode}{k}"
        for path in namespace.glob(f"*/*/{MANIFEST}"):
            state = json.loads(path.read_text(encoding="utf-8"))["state"]
            states[state] = states.get(state, 0) + 1
    result["rollout_states"] = states
    return result


def _smoke(root: Path) -> int:
    substrate = DeepSeekSubstrate()
    substrate.preflight()
    from types import SimpleNamespace

    source = tree.write_tree(
        substrate.surface(),
        {"systemprompt.md": "Follow the task. Use the write tool for requested text files."},
        root / "harness",
    )
    install = substrate.materialise(source, SimpleNamespace(install_mode="deepseek_profile"), {})
    try:
        run_dir = substrate.run_workspace(
            install,
            run_dir=root / "smoke",
            run_id="live-smoke",
            timeout=120,
            prompt="Write exactly DeepSeek Harness2 smoke to output/smoke.txt, then stop. "
            "Do not run shell commands or access the network.",
        )
        path = run_dir / "output" / "smoke.txt"
        if (
            not path.is_file()
            or path.read_text(encoding="utf-8").strip() != "DeepSeek Harness2 smoke"
        ):
            raise RuntimeError(f"Smoke did not deliver the expected file; inspect {run_dir}")
        print(f"Live DeepSeek smoke delivered the expected file. Evidence: {run_dir}")
        return 0
    finally:
        shutil.rmtree(install.staging_dir)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("run", "evaluate", "smoke"), default="run")
    parser.add_argument("--output", type=Path, default=Path("runs/deepseek-demo"))
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument("--mode", choices=("parallel", "sequential", "both"), default="both")
    parser.add_argument(
        "--live", action="store_true", help="Use a real paid endpoint (explicit opt-in)"
    )
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be positive")
    if args.command == "smoke" and not args.live:
        parser.error("smoke requires --live; ordinary run uses the credential-free local fixture")
    root = args.output.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.command == "smoke":
        return _smoke(root)
    modes = ("agg", "seq") if args.mode == "both" else (schedule.mode_token(args.mode),)
    path = root / "demo-config.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    backend = "live" if args.live else "local-fixture"
    if previous and previous["backend"] != backend:
        parser.error("Choose a new --output directory when changing the demo backend")
    if args.command == "evaluate":
        if not previous:
            parser.error("run the demo before evaluation")
        adapter = DeepSeekDemoAdapter(root, settings=DeepSeekSettings(**previous["settings"]))
        payload = evaluate(adapter, args.k, modes)
        return int(any(not row["graded"] for row in payload["adapted"]))
    if args.live:
        settings = DeepSeekSettings.from_env()
        if previous and previous["settings"] != asdict(settings):
            parser.error("Choose a new --output directory when changing runtime settings")
        adapter = DeepSeekDemoAdapter(root, settings=settings)
        adapter.substrate.preflight()
        write_json(path, {"backend": backend, "settings": asdict(settings)})
        result = _run(adapter, args.k, modes)
    else:
        port = urlsplit(previous["settings"]["base_url"]).port if previous else 0
        with MessagesFixture(port=port) as fixture:
            settings = DeepSeekSettings(
                base_url=fixture.url,
                reasoning_effort="off",
                max_tokens=1024,
                max_model_calls=6,
            )
            adapter = DeepSeekDemoAdapter(root, settings=settings, api_key=FIXTURE_KEY)
            adapter.substrate.preflight()
            write_json(path, {"backend": backend, "settings": asdict(settings)})
            result = _run(adapter, args.k, modes)
            result["fixture_requests"] = len(fixture.requests)
            result["fixture_errors"] = fixture.errors
    write_json(root / "demo.json", result)
    print(
        f"DeepSeek {backend}: {result['done']} tasks completed; {result['failed']} failed. "
        f"Evidence: {root}. No evaluation was performed."
    )
    incomplete = any(state != "completed" for state in result["rollout_states"])
    return int(result["failed"] > 0 or (not args.live and incomplete))


if __name__ == "__main__":
    raise SystemExit(main())
