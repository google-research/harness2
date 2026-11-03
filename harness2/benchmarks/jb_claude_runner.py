# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""JobBench launcher for the Claude Code substrate."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from harness2 import config
from harness2.benchmarks import jb_opencode_runner as runner
from harness2.substrate.claude import ClaudeSubstrate


@dataclass(frozen=True)
class Settings:
    task_ids: tuple[str, ...]
    label: str
    render_dir: Path
    tasks_base: Path
    model: str
    timeout_per_task: str
    max_retries: int
    log_dir: Path
    substrate: ClaudeSubstrate
    require_clean_exit: bool = True


def _settings() -> Settings:
    env = os.environ
    missing = [name for name in ("LABEL", "HARNESS_RENDER_DIR", "TASK_IDS") if not env.get(name)]
    if missing:
        raise SystemExit(f"[ERROR] unset: {', '.join(missing)}")
    tasks_base = Path(env.get("TASKS_BASE_DIR") or runner.JOBBENCH / "dataset" / "main")
    return Settings(
        task_ids=tuple(env["TASK_IDS"].split()),
        label=env["LABEL"],
        render_dir=Path(env["HARNESS_RENDER_DIR"]),
        tasks_base=tasks_base,
        model=env.get("HARNESS2_CLAUDE_MODEL") or config.SOLVER_MODEL_CC,
        timeout_per_task=env.get("TIMEOUT_PER_TASK") or "3600",
        max_retries=int(env.get("MAX_RETRIES") or 2),
        log_dir=tasks_base / ".harness2-logs",
        substrate=ClaudeSubstrate(),
    )


def _stage(task_dir: Path, render_dir: Path, workspace: Path) -> None:
    shutil.rmtree(workspace, ignore_errors=True)
    shutil.copytree(render_dir, workspace)
    (workspace / "output").mkdir()
    shutil.copytree(task_dir, workspace / "task_folder")


def _attempt(
    cfg: Settings, workspace: Path, traj_file: Path, log_file: Path, task_name: str, attempt: int
) -> int:
    prompt = runner.PROMPT.format(
        task_folder=workspace / "task_folder",
        output_dir=workspace / "output",
        workspace=workspace,
    )
    argv = ["timeout", "--kill-after=30s", cfg.timeout_per_task]
    argv += cfg.substrate.exec_argv(prompt, workspace, cfg.model)
    with (
        tempfile.TemporaryDirectory(prefix=f".{task_name}_a{attempt}.claude.") as home,
        traj_file.open("w", encoding="utf-8") as stdout,
        log_file.open("a", encoding="utf-8") as stderr,
    ):
        result = subprocess.run(
            argv,
            cwd=workspace,
            env=cfg.substrate.exec_env(os.environ, config_dir=Path(home), model=cfg.model),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
    if result.returncode == 0 and cfg.substrate.run_failed(traj_file):
        with traj_file.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "name": "ClaudeResult",
                            "data": {"message": "Claude stream did not end in a successful result"},
                        },
                    }
                )
                + "\n"
            )
        return 1
    return result.returncode


def main() -> int:
    cfg = _settings()
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    runner.TEMP_BASE.mkdir(parents=True, exist_ok=True)
    failed = [
        task_id
        for task_id in cfg.task_ids
        if not runner.run_one(cfg, task_id, stage=_stage, run=_attempt)
    ]
    if failed:
        print(f"no output for: {', '.join(failed)}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
