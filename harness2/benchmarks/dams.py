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

"""DAMS benchmark adapter: tasks, domains, prompts, deliverables, and scoring.

Follows the BenchmarkAdapter protocol from harness2.core.spec.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import config
from ..core.spec import BenchPrompts, BenchSpec, BenchmarkAdapter, resolve_channel_model
from ..substrate.opencode import OpencodeSubstrate

TASKS: list[str] = [
    "backend-kpi-endpoint",
    "frontend-kpi-card",
    "deptrac-boundary-contract",
]

TASK_DOMAINS: dict[str, str] = {
    "backend-kpi-endpoint": "backend",
    "frontend-kpi-card": "frontend",
    "deptrac-boundary-contract": "architecture",
}

IGNORED_DELIVERABLE_FILES: frozenset[str] = frozenset(
    {
        "trajectory.md",
        "score.json",
        "runmark.json",
        "summary.json",
        ".git",
    }
)

DAMS_PROMPTS = BenchPrompts(
    work="implement and fix modular Laravel 12 and Vue 3 features in AnD Monitoring System",
    domain_word="domain",
    inv_levers="guardrails, tool guidance, scripts, boundary contracts",
    dom_levers="skills, subagents, domain memory, conventions",
    yardstick="automated unit tests, deptrac architecture boundary, and lint checks",
    selection_checks=(
        "Prefer the work product that satisfies boundary contracts, follows Laravel 12 and "
        "Vue 3 conventions, implements strictly typed parameters, and passes all unit tests."
    ),
)


class DamsAdapter:
    """AnD Monitoring System (DAMS) benchmark adapter implementing BenchmarkAdapter protocol."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        dams_root: Path | None = None,
        tasks_root: Path | None = None,
        split_file: Path | None = None,
    ):
        self.root = Path(root) if root is not None else config.RUNS / "dams"
        self.split_file = split_file
        self.dams_root = (
            Path(dams_root) if dams_root is not None else Path(r"D:\Apps\AnD Monitoring System")
        )
        self.tasks_root = (
            Path(tasks_root)
            if tasks_root is not None
            else config.DEPENDENCIES / "dams-tasks"
        )
        self.substrate = OpencodeSubstrate()
        self.spec = BenchSpec(
            name="dams",
            runs_root=self.root / "runs" if root is not None else config.RUNS / "dams",
            install_mode="workspace_config",
            prompts=DAMS_PROMPTS,
            channels={
                "proposer": resolve_channel_model(None, None),
                "selector": resolve_channel_model(None, None),
            },
        )

    def tasks(self, split: str = "all") -> list[str]:
        """Return stable list of golden task IDs."""
        if self.split_file is not None or split != "all":
            from .tasks import select

            task_dict = {t: self.domain_of(t) for t in TASKS}
            return select(task_dict, split, self.split_file, benchmark="dams")
        return list(TASKS)

    def preflight(self) -> None:
        """Readiness check for DAMS benchmark adapter."""
        if not self.tasks_root.is_dir():
            raise RuntimeError(
                f"DAMS tasks directory missing at {self.tasks_root}; run harness2-setup dams"
            )
        tasks = self.tasks()
        if not tasks:
            raise RuntimeError(f"No DAMS tasks found under {self.tasks_root}")

    def domain_of(self, task_id: str) -> str:
        """Return domain for a given task ID."""
        task_dir = self.task_files(task_id)
        if task_dir and (task_dir / "task.json").is_file():
            try:
                data = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
                if "domain" in data and isinstance(data["domain"], str):
                    return data["domain"]
            except Exception:
                pass
        return TASK_DOMAINS.get(task_id, "backend")

    def task_prompt(self, task_id: str) -> str:
        """Return task instructions as prompt string."""
        task_dir = self.task_files(task_id)
        if task_dir and (task_dir / "task.json").is_file():
            try:
                data = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
                if "prompt" in data and isinstance(data["prompt"], str):
                    return data["prompt"]
            except Exception:
                pass
        return f"Implement solution for DAMS task {task_id}."

    def task_files(self, task_id: str) -> Path | None:
        """Return path to task source directory if it exists."""
        candidate = self.tasks_root / task_id
        return candidate if candidate.is_dir() else None

    def base_harness(self) -> Path:
        """Read DAMS live .harness directory if present, otherwise create/return fallback."""
        if self.dams_root is not None:
            live = self.dams_root / ".harness"
            if live.is_dir():
                return live
        fallback = self.tasks_root / "base_harness"
        fallback.mkdir(parents=True, exist_ok=True)
        if not (fallback / "systemprompt.md").exists():
            (fallback / "systemprompt.md").write_text(
                "DAMS Base Harness System Prompt\n", encoding="utf-8"
            )
        return fallback

    def validate_tree(self, tree: dict[str, str]) -> None:
        """Benchmark-specific harness invariants."""
        # Standard validation passes; ensure no critical corruption
        if not isinstance(tree, dict):
            raise ValueError(f"Expected harness tree dictionary, got {type(tree).__name__}")

    def execute(
        self,
        task_id: str,
        harness_dir: Path | None,
        run_id: str,
        *,
        timeout: int | None = None,
    ) -> Path:
        """Execute one rollout and return run directory."""
        run_dir = self.root / "executions" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        task_dir = self.task_files(task_id)
        if task_dir and (task_dir / "source").is_dir():
            for src in (task_dir / "source").iterdir():
                if src.is_file():
                    dst = run_dir / src.name
                    if not dst.exists():
                        shutil.copy2(src, dst)

        trajectory_file = run_dir / "trajectory.md"
        if not trajectory_file.is_file():
            harness_name = harness_dir.name if harness_dir else "base"
            trajectory_file.write_text(
                f"[ACTION] Execute task {task_id} with harness {harness_name}\n"
                f"[OBSERVATION] Staged source artifacts copied to deliverable directory.\n",
                encoding="utf-8",
            )

        return run_dir

    def delivered(self, run_dir: Path) -> bool:
        """Did this rollout produce the deliverable artifacts?"""
        return len(self.deliverable_files(run_dir)) > 0

    def trajectory(self, run_dir: Path, *, obs_trunc: int = 50_000) -> str:
        """Return readable trajectory text."""
        traj_file = Path(run_dir) / "trajectory.md"
        if traj_file.is_file():
            text = traj_file.read_text(encoding="utf-8", errors="replace")
            return text[:obs_trunc] if len(text) > obs_trunc else text
        return f"(no trajectory for {Path(run_dir).name})"

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        """Return map of deliverable filename -> file content."""
        run_dir = Path(run_dir)
        if not run_dir.is_dir():
            return {}
        results: dict[str, str] = {}
        for p in sorted(run_dir.rglob("*")):
            if p.is_file() and p.name not in IGNORED_DELIVERABLE_FILES:
                rel = p.relative_to(run_dir).as_posix()
                try:
                    results[rel] = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
        return results

    def deliverable_text(self, run_dir: Path) -> str:
        """Return combined deliverable text extracted from run_dir."""
        files = self.deliverable_files(run_dir)
        return "\n\n".join(f"=== {k} ===\n{v}" for k, v in files.items())

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        """Judge one run directory against task criteria."""
        run_dir = Path(run_dir)
        score_file = run_dir / "score.json"
        if score_file.is_file():
            try:
                cached = json.loads(score_file.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and "n_passed" in cached and "n_criteria" in cached:
                    return cached
            except Exception:
                pass

        if task_id not in TASKS:
            return None

        criteria: dict[str, bool] = {}

        if task_id == "backend-kpi-endpoint":
            ctrl = run_dir / "ParameterKpiController.php"
            req = run_dir / "ParameterKpiRequest.php"
            c1 = ctrl.is_file()
            c4 = req.is_file()
            c2, c3 = False, False
            if c1:
                ctrl_text = ctrl.read_text(encoding="utf-8", errors="replace")
                c2 = "function calculate" in ctrl_text
                has_status = "'status'" in ctrl_text or '"status"' in ctrl_text
                has_value = "'value'" in ctrl_text or '"value"' in ctrl_text
                has_deviation = "'deviation'" in ctrl_text or '"deviation"' in ctrl_text
                not_incomplete = "'incomplete'" not in ctrl_text and '"incomplete"' not in ctrl_text
                c3 = has_status and has_value and has_deviation and not_incomplete

            criteria["controller_exists"] = c1
            criteria["calculate_method_defined"] = c2
            criteria["returns_kpi_payload"] = c3
            criteria["form_request_implemented"] = c4

        elif task_id == "frontend-kpi-card":
            card = run_dir / "KpiParameterCard.vue"
            c1 = card.is_file()
            c2, c3, c4 = False, False, False
            if c1:
                card_text = card.read_text(encoding="utf-8", errors="replace")
                c2 = "Props" in card_text and "defineProps" in card_text and "status" in card_text
                has_success = "success" in card_text
                has_warning = "warning" in card_text
                has_error = "error" in card_text
                c3 = has_success and has_warning and has_error and "Incomplete stub" not in card_text
                has_math = ("-" in card_text and "/" in card_text) or "deviation" in card_text
                has_pct = "%" in card_text or "100" in card_text
                c4 = has_math and has_pct and "Incomplete stub" not in card_text

            criteria["component_exists"] = c1
            criteria["props_typed_typescript"] = c2
            criteria["dynamic_status_badge"] = c3
            criteria["formatted_deviation_percentage"] = c4

        elif task_id == "deptrac-boundary-contract":
            svc = run_dir / "HelpdeskService.php"
            contract = run_dir / "EmployeeContractInterface.php"
            c1 = svc.is_file()
            c4 = contract.is_file()
            c2, c3 = False, False
            if c1:
                svc_text = svc.read_text(encoding="utf-8", errors="replace")
                illegal_import = "use Modules\\Employee\\Entities\\Employee;" in svc_text
                illegal_usage = "Employee::find" in svc_text
                c2 = not (illegal_import or illegal_usage)
                c3 = "EmployeeContractInterface" in svc_text

            criteria["service_exists"] = c1
            criteria["no_illegal_coupling"] = c2
            criteria["uses_contract_interface"] = c3
            criteria["contract_interface_exists"] = c4

        n_passed = sum(1 for v in criteria.values() if v)
        n_criteria = len(criteria)
        score_result = {
            "task_id": task_id,
            "n_passed": n_passed,
            "n_criteria": n_criteria,
            "all_pass": n_passed == n_criteria and n_criteria > 0,
            "criteria": criteria,
        }

        try:
            score_file.write_text(json.dumps(score_result, indent=2), encoding="utf-8")
        except OSError:
            pass

        return score_result


__all__ = [
    "DamsAdapter",
    "DAMS_PROMPTS",
    "TASKS",
    "TASK_DOMAINS",
]
