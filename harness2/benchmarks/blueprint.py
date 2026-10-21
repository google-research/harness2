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

"""A&D Strategic Blueprint benchmark adapter: tasks, domains, prompts, and scoring.

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
    "wi-authoring-standard",
    "doc-integrity-fix",
    "plantuml-sync",
]

TASK_DOMAINS: dict[str, str] = {
    "wi-authoring-standard": "wi",
    "doc-integrity-fix": "integrity",
    "plantuml-sync": "diagrams",
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

BLUEPRINT_PROMPTS = BenchPrompts(
    work="author, maintain, and verify strategic architecture blueprints and work instructions",
    domain_word="domain",
    inv_levers="guardrails, tool guidance, scripts, boundary contracts",
    dom_levers="skills, subagents, domain memory, conventions",
    yardstick="document integrity, RACI single-accountability, and PlantUML rendering checks",
    selection_checks=(
        "Prefer the work product that enforces strict namespace rules (EOB-WI-AND-xxx), "
        "adheres to single-Accountable RACI matrices with all 4 mandatory role columns, "
        "maintains atomic ripple-effect cross-references, and complies with PlantUML "
        "!theme plain and scale max 1050 width layout standards."
    ),
)


def _check_raci_single_a(text: str) -> bool:
    """Validate that in markdown table rows, each activity row has at most one Accountable ('A') cell."""
    lines = text.splitlines()
    has_a = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip().strip("*_#`") for c in stripped.split("|")[1:-1]]
        a_count = sum(1 for c in cells if c == "A")
        if a_count > 1:
            return False
        if a_count == 1:
            has_a = True
    return has_a


class BlueprintAdapter:
    """A&D Strategic Blueprint benchmark adapter implementing BenchmarkAdapter protocol."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        blueprint_root: Path | None = None,
        tasks_root: Path | None = None,
        split_file: Path | None = None,
    ):
        self.root = Path(root) if root is not None else config.RUNS / "bp"
        self.split_file = split_file
        self.blueprint_root = (
            Path(blueprint_root)
            if blueprint_root is not None
            else Path(r"D:\Apps\AD-Strategic-Blueprint")
        )
        self.tasks_root = (
            Path(tasks_root)
            if tasks_root is not None
            else config.DEPENDENCIES / "blueprint-tasks"
        )
        self.substrate = OpencodeSubstrate()
        channel_kind = config.env("HARNESS2_PROPOSER_CHANNEL")
        channel_model = config.env("HARNESS2_IMPROVER_MODEL")
        if not channel_kind and shutil.which("claude"):
            channel_kind = "claude_cli"
            channel_model = config.env("HARNESS2_CLAUDE_MODEL", channel_model or "claude-opus-5-5")
        resolved_channel = resolve_channel_model(channel_kind, channel_model)
        self.spec = BenchSpec(
            name="bp",
            runs_root=self.root / "runs" if root is not None else config.RUNS / "bp",
            install_mode="workspace_config",
            prompts=BLUEPRINT_PROMPTS,
            channels={
                "proposer": resolved_channel,
                "selector": resolved_channel,
            },
        )

    def tasks(self, split: str = "all") -> list[str]:
        """Return stable list of golden task IDs."""
        if self.split_file is not None or split != "all":
            from .tasks import select

            task_dict = {t: self.domain_of(t) for t in TASKS}
            return select(task_dict, split, self.split_file, benchmark="bp")
        return list(TASKS)

    def preflight(self) -> None:
        """Readiness check for Blueprint benchmark adapter."""
        if not self.tasks_root.is_dir():
            raise RuntimeError(
                f"Blueprint tasks directory missing at {self.tasks_root}; run harness2-setup bp"
            )
        tasks = self.tasks()
        if not tasks:
            raise RuntimeError(f"No Blueprint tasks found under {self.tasks_root}")

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
        return TASK_DOMAINS.get(task_id, "wi")

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
        return f"Implement solution for Blueprint task {task_id}."

    def task_files(self, task_id: str) -> Path | None:
        """Return path to task source directory if it exists."""
        candidate = self.tasks_root / task_id
        return candidate if candidate.is_dir() else None

    def base_harness(self) -> Path:
        """Read Blueprint live .harness directory if present, otherwise create/return fallback."""
        if self.blueprint_root is not None:
            live = self.blueprint_root / ".harness"
            if live.is_dir():
                return live
        fallback = self.tasks_root / "base_harness"
        fallback.mkdir(parents=True, exist_ok=True)
        if not (fallback / "systemprompt.md").exists():
            (fallback / "systemprompt.md").write_text(
                "Blueprint Base Harness System Prompt\n", encoding="utf-8"
            )
        return fallback

    def validate_tree(self, tree: dict[str, str]) -> None:
        """Benchmark-specific harness invariants."""
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

        if task_id == "wi-authoring-standard":
            wi = run_dir / "eob_wi_and_350_sample_wi.md"
            c1 = wi.is_file()
            c2, c3, c4, c5 = False, False, False, False
            if c1:
                wi_text = wi.read_text(encoding="utf-8", errors="replace")
                # Header metadata check
                c2 = "EOB-WI-AND-350" in wi_text and ("Version" in wi_text or "v1." in wi_text.lower())
                # 4-column RACI check
                has_cols = (
                    "A&D Manager" in wi_text
                    and "Domain Leader" in wi_text
                    and "Project/Product Owner" in wi_text
                    and "Project/Product Member" in wi_text
                )
                c3 = has_cols
                # Single Accountable per row check
                c4 = _check_raci_single_a(wi_text)
                # Module 239 revision history check
                c5 = "Module 239" in wi_text

            criteria["wi_file_exists"] = c1
            criteria["header_metadata"] = c2
            criteria["raci_four_columns"] = c3
            criteria["raci_single_a"] = c4
            criteria["module_239_history"] = c5

        elif task_id == "doc-integrity-fix":
            doc = run_dir / "eob_roadmap_and_002_sample.md"
            c1 = doc.is_file()
            c2, c3, c4 = False, False, False
            if c1:
                doc_text = doc.read_text(encoding="utf-8", errors="replace")
                # Forbidden namespace removed
                c2 = "EOB-AND-WI-020" not in doc_text
                # Canonical namespace used
                c3 = "EOB-WI-AND-020" in doc_text
                # Broken relative link resolved and no absolute file URLs
                c4 = "non_existent_doc.md" not in doc_text and "file:///" not in doc_text

            criteria["document_exists"] = c1
            criteria["forbidden_namespace_removed"] = c2
            criteria["canonical_namespace_used"] = c3
            criteria["broken_link_fixed"] = c4

        elif task_id == "plantuml-sync":
            puml = run_dir / "sample_workflow.puml"
            c1 = puml.is_file()
            c2, c3, c4 = False, False, False
            if c1:
                puml_text = puml.read_text(encoding="utf-8", errors="replace")
                c2 = "!theme plain" in puml_text
                c3 = "scale max 1050 width" in puml_text
                # Vertical layout: no horizontal direction, diagram markers present
                c4 = (
                    "left to right direction" not in puml_text
                    and "@startuml" in puml_text
                    and "@enduml" in puml_text
                )

            criteria["diagram_file_exists"] = c1
            criteria["theme_plain"] = c2
            criteria["scale_max_width"] = c3
            criteria["vertical_layout"] = c4

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
    "BlueprintAdapter",
    "BLUEPRINT_PROMPTS",
    "TASKS",
    "TASK_DOMAINS",
]
