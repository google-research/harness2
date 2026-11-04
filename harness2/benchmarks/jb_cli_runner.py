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

"""JobBench launcher for Claude Code and Pi: stage a task workspace, run the CLI, keep artifacts.

Staging, retries and the output move are jb_opencode_runner's; this file only supplies the
two agent-specific steps. The substrate owns the CLI's argv and environment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from harness2 import config
from harness2.benchmarks import jb_opencode_runner as R
from harness2.substrate.claude import ClaudeSubstrate
from harness2.substrate.pi import PiSubstrate

AGENTS = {"cc": (ClaudeSubstrate, config.CLAUDE_MODEL), "pi": (PiSubstrate, config.PI_MODEL)}


@dataclass(frozen=True)
class Settings:
    task_ids: tuple[str, ...]
    label: str
    render_dir: Path
    tasks_base: Path
    substrate: ClaudeSubstrate | PiSubstrate
    model: str
    timeout_per_task: str
    max_retries: int
    log_dir: Path


def _settings() -> Settings:
    """Resolve the environment contract the adapter writes; a bad one exits non-zero."""
    env = os.environ
    missing = [n for n in ("JB_AGENT", "LABEL", "HARNESS_RENDER_DIR", "TASK_IDS") if not env.get(n)]
    if missing:
        raise SystemExit(f"[ERROR] unset: {', '.join(missing)}")
    if env["JB_AGENT"] not in AGENTS:
        raise SystemExit(
            f"[ERROR] JB_AGENT must be one of {tuple(AGENTS)}, got {env['JB_AGENT']!r}"
        )
    cls, model = AGENTS[env["JB_AGENT"]]
    tasks_base = Path(env.get("TASKS_BASE_DIR") or R.JOBBENCH / "dataset" / "main")
    return Settings(
        task_ids=tuple(env["TASK_IDS"].split()),
        label=env["LABEL"],
        render_dir=Path(env["HARNESS_RENDER_DIR"]),
        tasks_base=tasks_base,
        substrate=cls(),
        model=model,
        timeout_per_task=env.get("TIMEOUT_PER_TASK") or "3600",
        max_retries=int(env.get("MAX_RETRIES") or 2),
        log_dir=tasks_base / ".harness2-logs",
    )


def _stage(task_dir: Path, render_dir: Path, workspace: Path) -> None:
    """The staged root entries become the workspace root, beside task_folder and output."""
    shutil.rmtree(workspace, ignore_errors=True)
    shutil.copytree(render_dir, workspace)
    (workspace / "output").mkdir()
    shutil.copytree(task_dir, workspace / "task_folder")


def _attempt(
    cfg: Settings, workspace: Path, traj_file: Path, log_file: Path, task_name: str, attempt: int
) -> int:
    """Run the CLI once in a throwaway config dir; its JSONL stream lands in `traj_file`."""
    prompt = R.PROMPT.format(
        task_folder=workspace / "task_folder",
        output_dir=workspace / "output",
        workspace=workspace,
    )
    argv = ["timeout", "--kill-after=30s", cfg.timeout_per_task]
    argv += cfg.substrate.exec_argv(prompt, workspace, cfg.model)
    with (
        tempfile.TemporaryDirectory(prefix=f".{task_name}_a{attempt}.home.") as home,
        traj_file.open("w", encoding="utf-8") as out,
        log_file.open("a", encoding="utf-8") as err,
    ):
        code = subprocess.run(
            argv,
            cwd=workspace,
            env=cfg.substrate.exec_env(os.environ, config_dir=Path(home)),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
        ).returncode
    # The exit code alone misses a model error the stream reports (pi exits 0 after one).
    return 1 if code == 0 and cfg.substrate.run_failed(traj_file) else code


def main() -> int:
    cfg = _settings()
    print(f"=== harness eval: LABEL={cfg.label} AGENT={cfg.substrate.name} ===", flush=True)
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    R.TEMP_BASE.mkdir(parents=True, exist_ok=True)
    failed = [t for t in cfg.task_ids if not R.run_one(cfg, t, stage=_stage, run=_attempt)]
    print(f"=== harness eval done: {cfg.label} ===", flush=True)
    if failed:
        print(f"no output for: {', '.join(failed)}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
