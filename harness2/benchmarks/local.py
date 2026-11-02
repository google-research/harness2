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

"""Local benchmark adapter: run Harness² on an arbitrary local folder of tasks.

Task folder convention:
    <tasks-dir>/
    ├── bench.toml                 (optional metadata: work, domain_word, deliverable_word)
    └── <domain>/                  (practice area / occupation / category)
        └── <task_id>/
            ├── task.md            (required prompt instructions for the agent)
            ├── files/             (optional input documents or code)
            └── task.toml          (optional: outputs, check command, timeout)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.spec import BenchPrompts, BenchSpec, ChannelSpec, resolve_channel_model
from ..substrate.base import Install, RunRequest
from ..substrate.codex import CODEX_SURFACE, CodexSubstrate
from ..substrate.opencode import OPENCODE_SURFACE, OpencodeSubstrate
from .tasks import select


@dataclass(frozen=True)
class LocalTaskMeta:
    """Metadata from a task's optional task.toml."""

    outputs: tuple[str, ...] = ()
    check: tuple[str, ...] = ()
    timeout: int = 3600


DEFAULT_PROMPTS = BenchPrompts(
    work=(
        "Execution of professional tasks and technical assignments. The agent receives "
        "instructions, context and source files, and produces complete, immediately usable "
        "deliverables meeting all stated requirements."
    ),
    domain_word="domain",
    deliverable_word="deliverable",
    inv_levers=(
        "- inspect available source files and instruction requirements before drafting\n"
        "- produce deliverables in the exact requested file paths\n"
        "- verify that output files are complete and properly formatted before finishing"
    ),
    dom_levers=(
        "- follow domain-specific best practices and conventions\n"
        "- ensure internal consistency across all produced artifacts"
    ),
    yardstick=(
        "Did the run satisfy all instructions, write the requested output files, "
        "and ensure the resulting work is complete and usable?"
    ),
    selection_checks=(
        "Prefer the work product that strictly adheres to the prompt, produces all "
        "requested output files in usable form, and avoids syntax or calculation errors."
    ),
)


def discover_tasks(tasks_dir: Path) -> dict[str, str]:
    """Discover tasks in <tasks-dir>/<domain>/<task_name>/task.md.

    Returns sorted dict of { 'domain/task_name': 'domain' }.
    """
    tasks: dict[str, str] = {}
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"Tasks directory not found: {tasks_dir}")
    for domain_dir in sorted(tasks_dir.iterdir()):
        if not domain_dir.is_dir() or domain_dir.name.startswith("."):
            continue
        domain = domain_dir.name
        for task_dir in sorted(domain_dir.iterdir()):
            if not task_dir.is_dir() or task_dir.name.startswith("."):
                continue
            if (task_dir / "task.md").is_file():
                task_id = f"{domain}/{task_dir.name}"
                tasks[task_id] = domain
    if not tasks:
        raise FileNotFoundError(
            f"No tasks found in {tasks_dir}. Expected layout: <tasks-dir>/<domain>/<task_id>/task.md"
        )
    return tasks


def compute_inputs_fingerprint(task_prompt: str, files_dir: Path | None) -> str:
    """A stable digest of what the agent was asked and what it was given.

    Adopts PR #76's convention: SHA-1 of the prompt text followed by sorted relative paths
    and bytes of files in files_dir.
    """
    h = hashlib.sha1()
    h.update(b"prompt\0")
    h.update(str(task_prompt).encode("utf-8"))
    h.update(b"\0")
    if files_dir is not None and files_dir.is_dir():
        for p in sorted(files_dir.rglob("*")):
            if not p.is_file() or RM._ignored(p):
                continue
            h.update(str(p.relative_to(files_dir)).encode("utf-8"))
            h.update(b"\0")
            try:
                h.update(p.read_bytes())
            except OSError:
                h.update(b"<unreadable>")
            h.update(b"\0")
    return h.hexdigest()[:16]


class LocalAdapter:
    """The BenchmarkAdapter implementation for arbitrary local tasks."""

    def __init__(
        self,
        tasks_dir: Path | str,
        *,
        substrate: str = "oc",
        model: str | None = None,
        timeout: int | None = None,
        base_harness: Path | str | None = None,
        split_file: Path | None = None,
        runs_root: Path | None = None,
    ):
        self.tasks_dir = Path(tasks_dir).expanduser().resolve()
        self.split_file = split_file
        self.timeout = int(timeout or 3600)
        self._custom_base_harness = Path(base_harness).resolve() if base_harness else None

        # Discover tasks
        self._discovered = discover_tasks(self.tasks_dir)

        # Scoped hash to ensure namespaces never collide across different task folders
        tasks_hash = hashlib.sha256(str(self.tasks_dir).encode("utf-8")).hexdigest()[:8]
        bench_name = f"local-{tasks_hash}"

        # Resolve runs root
        self._runs_root = (
            Path(runs_root).resolve()
            if runs_root is not None
            else config.RUNS / bench_name
        )
        self._results_root = self._runs_root / "results" / substrate

        # Load optional bench.toml prompts
        prompts = self._load_bench_prompts()

        # Channel resolution
        channel_proposer = resolve_channel_model(None, None)
        self.spec = BenchSpec(
            name=bench_name,
            runs_root=self._runs_root,
            install_mode="workspace_agents_md" if substrate == "oc" else "codex_root",
            prompts=prompts,
            channels={"proposer": channel_proposer, "selector": channel_proposer},
            domain_word=prompts.domain_word,
        )

        # Substrate initialization with concrete launchers
        if substrate == "oc":
            self.substrate = OpencodeSubstrate(launcher=self._launch_opencode)
            self.model = model or config.SOLVER_MODEL_OC
        elif substrate == "cx":
            self.substrate = CodexSubstrate(launcher=self._launch_codex)
            self.model = model or config.SOLVER_MODEL_CX
        else:
            raise ValueError(f"Unknown substrate {substrate!r}; expected 'oc' or 'cx'")

    def _load_bench_prompts(self) -> BenchPrompts:
        bench_toml = self.tasks_dir / "bench.toml"
        if not bench_toml.is_file():
            return DEFAULT_PROMPTS
        try:
            data = tomllib.loads(bench_toml.read_text(encoding="utf-8"))
            return BenchPrompts(
                work=str(data.get("work", DEFAULT_PROMPTS.work)),
                domain_word=str(data.get("domain_word", DEFAULT_PROMPTS.domain_word)),
                deliverable_word=str(data.get("deliverable_word", DEFAULT_PROMPTS.deliverable_word)),
                inv_levers=str(data.get("inv_levers", DEFAULT_PROMPTS.inv_levers)),
                dom_levers=str(data.get("dom_levers", DEFAULT_PROMPTS.dom_levers)),
                yardstick=str(data.get("yardstick", DEFAULT_PROMPTS.yardstick)),
                selection_checks=str(data.get("selection_checks", DEFAULT_PROMPTS.selection_checks)),
            )
        except Exception:
            return DEFAULT_PROMPTS

    def _task_dir(self, task_id: str) -> Path:
        return self.tasks_dir / task_id

    def tasks(self, split: str = "all") -> list[str]:
        return select(self._discovered, split, self.split_file, benchmark=self.spec.name)

    def domain_of(self, task_id: str) -> str:
        return self._discovered.get(task_id, task_id.split("/", 1)[0])

    def task_prompt(self, task_id: str) -> str:
        p = self._task_dir(task_id) / "task.md"
        if not p.is_file():
            raise FileNotFoundError(f"task.md missing for task {task_id}: {p}")
        return p.read_text(encoding="utf-8")

    def task_files(self, task_id: str) -> Path | None:
        files_dir = self._task_dir(task_id) / "files"
        return files_dir if files_dir.is_dir() else None

    def task_meta(self, task_id: str) -> LocalTaskMeta:
        toml_path = self._task_dir(task_id) / "task.toml"
        if not toml_path.is_file():
            return LocalTaskMeta()
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid task.toml for task {task_id}: {exc}") from exc
        raw_check = data.get("check")
        if isinstance(raw_check, list):
            check_cmd = tuple(str(x) for x in raw_check)
        elif isinstance(raw_check, str) and raw_check.strip():
            check_cmd = tuple(raw_check.strip().split())
        else:
            check_cmd = ()
        raw_outputs = data.get("outputs", ())
        outputs = tuple(str(x) for x in raw_outputs)
        timeout = int(data.get("timeout", self.timeout))
        return LocalTaskMeta(outputs=outputs, check=check_cmd, timeout=timeout)

    def task_fingerprint(self, task_id: str) -> str:
        return compute_inputs_fingerprint(self.task_prompt(task_id), self.task_files(task_id))

    def base_harness(self) -> Path:
        if self._custom_base_harness:
            if not HT.is_tree(self.substrate.surface(), self._custom_base_harness):
                raise ValueError(
                    f"Custom base harness at {self._custom_base_harness} is not a valid "
                    f"{self.substrate.surface().name} tree"
                )
            return self._custom_base_harness

        base_dir = self.tasks_dir / ".base_harness" / self.substrate.name
        if not HT.is_tree(self.substrate.surface(), base_dir):
            base_dir.mkdir(parents=True, exist_ok=True)
            surface = self.substrate.surface()
            base_files = {name: "" for name in surface.always_on}
            if self.substrate.name == "oc":
                base_files["systemprompt.md"] = (
                    "You are an expert autonomous software and task execution agent.\n"
                    "Read task instructions carefully, follow all requirements, and write "
                    "deliverables to their expected paths.\n"
                )
                base_files["guardrails.md"] = (
                    "- Always verify that requested deliverables are created and non-empty.\n"
                    "- Do not alter source input files unless instructed.\n"
                )
            else:
                base_files["AGENTS.md"] = (
                    "You are an expert autonomous software and task execution agent.\n"
                    "Read task instructions carefully, follow all requirements, and write "
                    "deliverables to their expected paths.\n"
                    "- Always verify that requested deliverables are created and non-empty.\n"
                )
            HT.write_tree(surface, base_files, base_dir, stubs=True)
        return base_dir

    def validate_tree(self, harness: HT.Tree) -> None:
        surface = self.substrate.surface()
        for req in surface.always_on:
            if req not in harness and surface.reject_empty_always_on:
                raise ValueError(f"Proposed harness tree is missing required always-on file: {req}")

    def _run_dir(self, run_id: str) -> Path:
        return self._results_root / run_id

    def _install(self, harness_dir: Path | None) -> Install:
        if harness_dir is None:
            return Install(
                staging_dir=self.base_harness(), fingerprint="base", extras={"harness": "base"}
            )
        inst = self.substrate.materialise(Path(harness_dir), self.spec, {})
        return Install(
            staging_dir=inst.staging_dir,
            fingerprint=inst.fingerprint,
            extras={**inst.extras, "harness": "staged"},
        )

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        out = self._run_dir(run_id)
        inputs_digest = self.task_fingerprint(task_id)

        # Check for clean resume: both harness and task inputs must match
        if RM.is_complete(out, harness_dir=harness_dir):
            mark = RM.read_mark(out)
            if mark and mark.get("inputs") == inputs_digest:
                return out
            if out.is_dir():
                shutil.rmtree(out, ignore_errors=True)

        install = self._install(harness_dir)
        try:
            dirs = self.substrate.execute(task_id, install, [run_id], int(timeout or self.timeout))
        finally:
            if install.extras.get("harness") == "staged":
                shutil.rmtree(install.staging_dir, ignore_errors=True)

        out.mkdir(parents=True, exist_ok=True)
        RM.mark_complete(
            out,
            run_id=run_id,
            harness_dir=harness_dir,
            extra={
                "inputs": inputs_digest,
                "task_id": task_id,
            },
        )
        return dirs[0]

    def _launch_opencode(self, req: RunRequest, install: Install) -> list[Path]:
        task_id = req.task_ref
        dirs: list[Path] = []
        for run_id in req.run_ids:
            run_dir = self._run_dir(run_id)
            workspace = run_dir / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            meta = self.task_meta(task_id)
            (run_dir / "_task_meta.json").write_text(
                json.dumps({"outputs": list(meta.outputs), "timeout": meta.timeout}),
                encoding="utf-8",
            )

            # Copy input files fresh into isolated workspace
            input_files: list[str] = []
            files_dir = self.task_files(task_id)
            if files_dir is not None and files_dir.is_dir():
                for p in sorted(files_dir.rglob("*")):
                    if not p.is_file() or RM._ignored(p):
                        continue
                    rel = HT.normalize_rel(str(p.relative_to(files_dir)))
                    dest = workspace / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
                    input_files.append(rel)
            (run_dir / "_input_files.json").write_text(json.dumps(input_files), encoding="utf-8")

            # Write task instructions into workspace
            (workspace / "task.md").write_text(self.task_prompt(task_id), encoding="utf-8")

            # Install harness into workspace
            for p in sorted(install.staging_dir.rglob("*")):
                if not p.is_file() or RM._ignored(p):
                    continue
                rel = HT.normalize_rel(str(p.relative_to(install.staging_dir)))
                dest = workspace / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)

            # Run execution
            self._execute_rollout(
                workspace=workspace,
                run_dir=run_dir,
                prompt=self.task_prompt(task_id),
                timeout=req.timeout,
            )
            dirs.append(run_dir)
        return dirs

    def _launch_codex(self, req: RunRequest, install: Install) -> list[Path]:
        task_id = req.task_ref
        dirs: list[Path] = []
        for run_id in req.run_ids:
            run_dir = self._run_dir(run_id)
            workspace = run_dir / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            meta = self.task_meta(task_id)
            (run_dir / "_task_meta.json").write_text(
                json.dumps({"outputs": list(meta.outputs), "timeout": meta.timeout}),
                encoding="utf-8",
            )

            input_files: list[str] = []
            files_dir = self.task_files(task_id)
            if files_dir is not None and files_dir.is_dir():
                for p in sorted(files_dir.rglob("*")):
                    if not p.is_file() or RM._ignored(p):
                        continue
                    rel = HT.normalize_rel(str(p.relative_to(files_dir)))
                    dest = workspace / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
                    input_files.append(rel)
            (run_dir / "_input_files.json").write_text(json.dumps(input_files), encoding="utf-8")

            (workspace / "task.md").write_text(self.task_prompt(task_id), encoding="utf-8")

            # Install codex harness
            for p in sorted(install.staging_dir.rglob("*")):
                if not p.is_file() or RM._ignored(p):
                    continue
                rel = HT.normalize_rel(str(p.relative_to(install.staging_dir)))
                dest = workspace / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)

            self._execute_rollout(
                workspace=workspace,
                run_dir=run_dir,
                prompt=self.task_prompt(task_id),
                timeout=req.timeout,
            )
            dirs.append(run_dir)
        return dirs

    def _execute_rollout(
        self, *, workspace: Path, run_dir: Path, prompt: str, timeout: int
    ) -> None:
        """Run the substrate agent in the isolated workspace and capture artifacts."""
        log_file = run_dir / "execution.log"
        stream_file = run_dir / (
            "session.jsonl" if self.substrate.name == "oc" else "events.jsonl"
        )
        traj_file = run_dir / "trajectory.md"

        # Check for mock / test execution hook
        mock_runner = os.environ.get("HARNESS2_LOCAL_MOCK_RUNNER")
        if mock_runner:
            res = subprocess.run(
                [sys.executable, mock_runner, str(workspace), str(run_dir)],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            log_file.write_text(res.stdout + "\n" + res.stderr, encoding="utf-8")
            if not traj_file.is_file():
                traj_file.write_text(
                    f"[ACTION] Execute mock runner\n[OBSERVATION]\n{res.stdout}",
                    encoding="utf-8",
                )
            return

        # Real agent execution
        if self.substrate.name == "oc":
            argv = self.substrate.run_argv(
                prompt=prompt,
                workspace=workspace,
                model=self.model,
            )
        else:
            argv = [
                str(Path(config.CODEX_DIST) / "codex"),
                "exec",
                "-C",
                str(workspace),
                "--dangerously-bypass-approvals-and-run-arbitrary-cmds",
                prompt,
            ]

        with open(log_file, "w", encoding="utf-8") as lf:
            proc = subprocess.run(
                argv,
                cwd=workspace,
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )

        if not traj_file.is_file():
            if stream_file.is_file():
                traj = self.substrate.read_trajectory(
                    stream_file, obs_trunc=self.spec.obs_trunc, max_chars=self.spec.obs_trunc
                )
            else:
                log_text = log_file.read_text(encoding="utf-8", errors="replace")[: self.spec.obs_trunc]
                traj = f"[ACTION] Execute agent in workspace\n[OBSERVATION]\n{log_text}"
            traj_file.write_text(traj, encoding="utf-8")

    def _is_harness_file(self, rel: str) -> bool:
        surface = self.substrate.surface()
        if rel in surface.always_on:
            return True
        for stub_path, _ in surface.stubs:
            if rel == stub_path:
                return True
        if surface.skill_dir and rel.startswith(surface.skill_dir + "/"):
            return True
        if surface.plugin_dir and rel.startswith(surface.plugin_dir + "/"):
            return True
        if rel.startswith("scripts/") or rel.startswith("sub_agents/"):
            return True
        if rel == "task.md":
            return True
        return False

    def delivered(self, run_dir: Path) -> bool:
        workspace = run_dir / "workspace"
        if not workspace.is_dir():
            return False

        meta_path = run_dir / "_task_meta.json"
        outputs = []
        if meta_path.is_file():
            try:
                outputs = json.loads(meta_path.read_text(encoding="utf-8")).get("outputs", [])
            except Exception:
                outputs = []

        if outputs:
            for out_rel in outputs:
                target = workspace / out_rel
                if not target.is_file() or target.stat().st_size == 0:
                    return False
            return True

        input_files_rec = run_dir / "_input_files.json"
        inputs = set()
        if input_files_rec.is_file():
            try:
                inputs = set(json.loads(input_files_rec.read_text(encoding="utf-8")))
            except Exception:
                inputs = set()

        for p in workspace.rglob("*"):
            if not p.is_file() or RM._ignored(p):
                continue
            rel = HT.normalize_rel(str(p.relative_to(workspace)))
            if rel in inputs or self._is_harness_file(rel):
                continue
            if p.stat().st_size > 0:
                return True
        return False

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        workspace = run_dir / "workspace"
        if not workspace.is_dir():
            return {}

        meta_path = run_dir / "_task_meta.json"
        outputs = []
        if meta_path.is_file():
            try:
                outputs = json.loads(meta_path.read_text(encoding="utf-8")).get("outputs", [])
            except Exception:
                outputs = []

        result: dict[str, str] = {}
        if outputs:
            for out_rel in outputs:
                target = workspace / out_rel
                if target.is_file():
                    try:
                        result[out_rel] = target.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        result[out_rel] = f"<{target.stat().st_size} bytes binary>"
            return result

        input_files_rec = run_dir / "_input_files.json"
        inputs = set()
        if input_files_rec.is_file():
            try:
                inputs = set(json.loads(input_files_rec.read_text(encoding="utf-8")))
            except Exception:
                inputs = set()

        for p in sorted(workspace.rglob("*")):
            if not p.is_file() or RM._ignored(p):
                continue
            rel = HT.normalize_rel(str(p.relative_to(workspace)))
            if rel in inputs or self._is_harness_file(rel):
                continue
            try:
                result[rel] = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                result[rel] = f"<{p.stat().st_size} bytes binary>"
        return result

    def deliverable_text(self, run_dir: Path) -> str:
        files = self.deliverable_files(run_dir)
        if not files:
            return ""
        if len(files) == 1:
            return next(iter(files.values()))
        parts = []
        for name, content in sorted(files.items()):
            parts.append(f"### {name}\n\n{content}")
        return "\n\n".join(parts)

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        traj_path = run_dir / "trajectory.md"
        if traj_path.is_file():
            return traj_path.read_text(encoding="utf-8", errors="replace")[:obs_trunc]
        stream_file = run_dir / (
            "session.jsonl" if self.substrate.name == "oc" else "events.jsonl"
        )
        if stream_file.is_file():
            return self.substrate.read_trajectory(
                stream_file, obs_trunc=obs_trunc, max_chars=obs_trunc
            )
        return "(trajectory unavailable)"

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        """Judge one run after selection.

        If a check command is configured in task.toml, execute it locally.
        If no check command is configured, explicitly report not_configured
        so judge/report treats it as ungraded rather than a judge crash.
        """
        meta = self.task_meta(task_id)
        if not meta.check:
            return {
                "status": "not_configured",
                "n_passed": None,
                "n_criteria": 0,
                "all_pass": None,
            }

        workspace = run_dir / "workspace"
        if not workspace.is_dir():
            return {
                "status": "error",
                "error": "Workspace directory missing",
                "n_passed": 0,
                "n_criteria": 1,
                "all_pass": False,
            }

        cmd = list(meta.check)
        timeout = min(meta.timeout, 600)
        try:
            proc = subprocess.run(
                cmd,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            passed = proc.returncode == 0
            return {
                "status": "completed",
                "n_passed": 1 if passed else 0,
                "n_criteria": 1,
                "all_pass": passed,
                "returncode": proc.returncode,
                "stdout": proc.stdout[:2000],
                "stderr": proc.stderr[:2000],
            }
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "error": f"Verification command timed out after {timeout}s",
                "n_passed": 0,
                "n_criteria": 1,
                "all_pass": False,
            }
        except Exception as exc:
            return {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "n_passed": 0,
                "n_criteria": 1,
                "all_pass": False,
            }

    def preflight(self) -> None:
        """Substrate health check before rolling out."""
        self.substrate.preflight()
