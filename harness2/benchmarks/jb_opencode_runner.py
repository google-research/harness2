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

"""JobBench OpenCode launcher: stage a task workspace, run OpenCode in it, keep the artifacts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

JOBBENCH = Path(__file__).resolve().parents[1]
TEMP_BASE = Path("/tmp/hevo_ws")
TRAJ_DIR_NAME = "model_traj"
EVENT_PREFIX = '{"type":'

PROMPT = """=== TASK FOLDER ===
{task_folder}

=== INSTRUCTIONS ===
1. Read the TASK_INSTRUCTIONS.txt file in the task folder above
2. Based on the Reference Files section in TASK_INSTRUCTIONS.txt, read the corresponding files from the same task folder using appropriate tools.
3. Complete the task as specified in TASK_INSTRUCTIONS.txt
4. Only save the final deliverables to the output directory specified below. Do not save any intermediate or temporary files.

=== OUTPUT DIRECTORY ===
{output_dir}

IMPORTANT:
- All reference files are in the task folder: {task_folder}
- Only save the final deliverables to the output directory {output_dir}. Do not save any intermediate or temporary files.
- You MUST only access files within {workspace} or search online for new reference files if you find needed. Do NOT access any files or directories in this system outside of this path.
- If you encounter ambiguous or conflicting information, analyze the conflict, explain your reasoning, and justify the approach you choose.
- If a file cannot be read directly (e.g., .xlsx, .docx, .db, .pptx), use appropriate tools, MCP servers, or code to extract and process its contents."""


@dataclass(frozen=True)
class Settings:
    task_ids: tuple[str, ...]
    label: str
    render_dir: Path
    tasks_base: Path
    opencode_dir: Path
    model_id: str
    agent_name: str
    variant: str
    agent_steps: str
    timeout_per_task: str
    max_retries: int
    log_dir: Path


def _settings() -> Settings:
    """Resolve the environment contract the adapter writes; a bad one exits non-zero."""
    env = os.environ
    missing = [n for n in ("MODEL_ID", "LABEL", "HARNESS_RENDER_DIR", "TASK_IDS") if not env.get(n)]
    if missing:
        raise SystemExit(f"[ERROR] unset: {', '.join(missing)}")
    render_dir = Path(env["HARNESS_RENDER_DIR"])
    if not (render_dir / ".opencode").is_dir():
        raise SystemExit(f"[ERROR] HARNESS_RENDER_DIR has no .opencode/: {render_dir}")
    opencode_dir = Path(env.get("OPENCODE_DIR") or JOBBENCH / "opencode")
    if not (opencode_dir / "packages" / "opencode").is_dir():
        raise SystemExit(f"[ERROR] OpenCode not installed at {opencode_dir}")
    tasks_base = Path(env.get("TASKS_BASE_DIR") or JOBBENCH / "dataset" / "main")
    return Settings(
        task_ids=tuple(env["TASK_IDS"].split()),
        label=env["LABEL"],
        render_dir=render_dir,
        tasks_base=tasks_base,
        opencode_dir=opencode_dir,
        model_id=env["MODEL_ID"],
        agent_name=env.get("AGENT_NAME", ""),
        variant=env.get("VARIANT", ""),
        agent_steps=env.get("AGENT_STEPS", ""),
        timeout_per_task=env.get("TIMEOUT_PER_TASK") or "3600",
        max_retries=int(env.get("MAX_RETRIES") or 2),
        log_dir=tasks_base / ".harness2-logs",
    )


def _hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _nonempty(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _log(log_file: Path, line: str) -> None:
    print(line, flush=True)
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _permission(workspace: Path) -> str:
    return json.dumps(
        {
            "external_directory": {
                "*": "deny",
                "/tmp": "allow",
                "/tmp/*": "allow",
                "/tmp/**": "allow",
                f"{TEMP_BASE}/*": "allow",
                f"{workspace}/**": "allow",
            }
        }
    )


def _reset_task(task_dir: Path, workspace: Path) -> None:
    """Restore a pristine task_folder and an empty output/ before a retry."""
    shutil.rmtree(workspace / "task_folder", ignore_errors=True)
    shutil.rmtree(workspace / "output", ignore_errors=True)
    shutil.copytree(task_dir, workspace / "task_folder")
    (workspace / "output").mkdir(parents=True, exist_ok=True)


def _stage(task_dir: Path, render_dir: Path, workspace: Path) -> None:
    shutil.rmtree(workspace, ignore_errors=True)
    (workspace / "output").mkdir(parents=True)
    shutil.copytree(task_dir, workspace / "task_folder")
    shutil.copytree(render_dir / ".opencode", workspace / ".opencode")
    if (render_dir / "AGENTS.md").is_file():
        shutil.copy(render_dir / "AGENTS.md", workspace / "AGENTS.md")
    if (render_dir / "scripts").is_dir():
        shutil.copytree(render_dir / "scripts", workspace / "scripts")


def _command(cfg: Settings, workspace: Path, prompt: str, title: str) -> list[str]:
    cmd = [
        "timeout",
        "--kill-after=30s",
        cfg.timeout_per_task,
        "bun",
        "run",
        "--cwd",
        str(cfg.opencode_dir / "packages" / "opencode"),
        "--conditions=browser",
        "src/index.ts",
        "run",
        prompt,
        "--dir",
        str(workspace),
    ]
    if cfg.agent_name:
        cmd += ["--agent", cfg.agent_name]
    if cfg.variant:
        cmd += ["--variant", cfg.variant]
    return cmd + ["--model", cfg.model_id, "--title", title, "--format", "json"]


def _attempt(
    cfg: Settings, workspace: Path, traj_file: Path, log_file: Path, task_name: str, attempt: int
) -> int:
    """Run OpenCode once; its event stream lands in `traj_file`, everything else in the log."""
    if cfg.agent_steps and not (workspace / "opencode.json").is_file():
        (workspace / "opencode.json").write_text(
            json.dumps(
                {"agent": {"build": {"steps": json.loads(cfg.agent_steps)}}},
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
    prompt = PROMPT.format(
        task_folder=workspace / "task_folder",
        output_dir=workspace / "output",
        workspace=workspace,
    )
    title = f"{task_name} | {cfg.label} | a{attempt}"
    handle, raw = tempfile.mkstemp(prefix=f".{task_name}_a{attempt}.stdout.", dir=traj_file.parent)
    os.close(handle)
    with open(raw, "w", encoding="utf-8") as out, log_file.open("a", encoding="utf-8") as err:
        code = subprocess.run(
            _command(cfg, workspace, prompt, title),
            cwd=cfg.opencode_dir,
            env={**os.environ, "OPENCODE_PERMISSION": _permission(workspace)},
            stdout=out,
            stderr=err,
        ).returncode
    with open(raw, encoding="utf-8", errors="replace") as src:
        traj_file.write_text(
            "".join(ln for ln in src if ln.startswith(EVENT_PREFIX)), encoding="utf-8"
        )
    os.unlink(raw)
    return code


def run_one(cfg: Settings, task_id: str) -> bool:
    """Roll one task; the existing-output skip mirrors the adapter's resume, for direct runs.

    True only when deliverables landed in `model_output/<label>/`. A rollout that dies
    on auth or the proxy still writes a trajectory, so the caller must not read one as
    evidence that the agent ran: `main` turns a False here into a non-zero exit, which
    is what stops the adapter marking an output-less attempt complete and scoring it as
    a real zero.
    """
    task_parent = cfg.tasks_base / task_id
    task_dir = task_parent / "task_folder"
    task_name = task_id.replace("/", "_")
    final_output_dir = task_parent / "model_output" / cfg.label
    traj_dir = task_parent / TRAJ_DIR_NAME / cfg.label
    log_file = cfg.log_dir / f"hevo_{task_name}_{cfg.label}_{_stamp()}.log"

    if not task_dir.is_dir():
        _log(log_file, f"[ERROR] no task_folder: {task_dir}")
        return False
    if _nonempty(final_output_dir):
        print(f"[{_hms()}] SKIP (done): {task_id} ({cfg.label})", flush=True)
        return True
    traj_dir.mkdir(parents=True, exist_ok=True)

    workspace = TEMP_BASE / f"{task_name}_{cfg.label}_{os.getpid()}"
    output_dir = workspace / "output"
    _stage(task_dir, cfg.render_dir, workspace)

    for attempt in range(1, cfg.max_retries + 1):
        if attempt > 1:
            _reset_task(task_dir, workspace)
        traj_file = traj_dir / f"{task_name}_{cfg.label}_attempt{attempt}_{_stamp()}.jsonl"
        _log(log_file, f"[{_hms()}] run {task_id} ({cfg.label}) attempt {attempt} -> {traj_file}")
        code = _attempt(cfg, workspace, traj_file, log_file, task_name, attempt)
        if _nonempty(output_dir):
            break
        if code in (124, 137):
            _log(log_file, f"[{_hms()}] TIMEOUT {task_id} attempt {attempt}")

    delivered = _nonempty(output_dir)
    if delivered:
        final_output_dir.mkdir(parents=True, exist_ok=True)
        for item in sorted(output_dir.iterdir()):
            if not item.name.startswith("."):
                shutil.move(str(item), str(final_output_dir / item.name))
        _log(log_file, f"[{_hms()}] output -> {final_output_dir}")
    else:
        _log(log_file, f"[{_hms()}] NO OUTPUT for {task_id} ({cfg.label})")
    shutil.rmtree(workspace, ignore_errors=True)
    return delivered and _nonempty(final_output_dir)


def main() -> int:
    cfg = _settings()
    print(
        f"=== harness eval: LABEL={cfg.label} MODEL={cfg.model_id} AGENT={cfg.agent_name} ===",
        flush=True,
    )
    print(f"render: {cfg.render_dir}", flush=True)
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    TEMP_BASE.mkdir(parents=True, exist_ok=True)
    failed = [task_id for task_id in cfg.task_ids if not run_one(cfg, task_id)]
    print(f"=== harness eval done: {cfg.label} ===", flush=True)
    if failed:
        print(f"no output for: {', '.join(failed)}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
