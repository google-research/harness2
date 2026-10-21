# Harness² Integration for AnD Monitoring System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Scenario B (Modular C1–C8 tree, router `AGENTS.md`, and on-disk Experience Library in `AnD Monitoring System`) and Scenario A (`DamsAdapter` benchmark suite with 3 Golden Tasks in `harness2`).

**Architecture:** 
1. In `AnD Monitoring System`, decompose the 50KB `AGENTS.md` into a modular `.harness/` tree linked to `.claude/` as the single source of truth, leaving a lean ~100-line router in `AGENTS.md`.
2. In `harness2`, implement `DamsAdapter` adhering to `BenchmarkAdapter` protocol (`harness2/core/spec.py`), with 3 isolated golden tasks (`backend-kpi-endpoint`, `frontend-kpi-card`, `deptrac-boundary-contract`), and wire into `harness2-setup`, `harness2-check`, and `harness2` CLI commands.

**Tech Stack:** Python 3.12 (Harness² core, unittest, subprocess), PowerShell / Git (Windows junctions, hooks), PHP 8.2 / Laravel 12 / Pest / Deptrac (DAMS backend), Vue 3 / Vuetify 3 / Vitest (DAMS frontend).

## Global Constraints

- Never break existing Claude Code workflows: `.claude/settings.json`, `.claude/rules/`, and `.claude/hooks/` remain the master source of truth.
- In `AnD Monitoring System`, all git changes must comply with project hygiene rules (no AI co-author trailers, no "generated with" footers, ASCII-only comments).
- Commit messages touching DAMS must carry `[ci] [cd]` markers.
- In `harness2`, all code must pass existing regressions (`python -m unittest tests/test_workflow.py`).

---

### Task 1: Scaffold DAMS `.harness/` Component Tree and Experience Library

**Files:**
- Create: `D:\Apps\AnD Monitoring System\.harness\systemprompt.md`
- Create: `D:\Apps\AnD Monitoring System\.harness\guardrails.md`
- Create: `D:\Apps\AnD Monitoring System\.harness\tool_guidance.md`
- Create: `D:\Apps\AnD Monitoring System\.harness\experience\INDEX.md`
- Create: `D:\Apps\AnD Monitoring System\.harness\experience\backend\EXPERIENCE.md`
- Create: `D:\Apps\AnD Monitoring System\.harness\experience\frontend\EXPERIENCE.md`
- Create: `D:\Apps\AnD Monitoring System\.harness\experience\architecture\EXPERIENCE.md`

**Interfaces:**
- Consumes: Rules extracted from `D:\Apps\AnD Monitoring System\.claude\CLAUDE.md` and `.claude\rules\*.md`.
- Produces: Modular C1–C4 markdown files and the on-disk Experience Library structure.

- [ ] **Step 1: Create `systemprompt.md` [C1]**
Write core mission, architecture overview (PIMS, 5 BSC dimensions, Laravel 12 + Vue 3), and executor role into `D:\Apps\AnD Monitoring System\.harness\systemprompt.md`.

- [ ] **Step 2: Create `guardrails.md` [C2]**
Extract invariant hard rules into `D:\Apps\AnD Monitoring System\.harness\guardrails.md`:
  - Mandatory isolated worktree in `.worktrees/<slug>` (never edit root).
  - Mandatory `[ci] [cd]` commit markers on HEAD commit.
  - Strict hygiene (HYG-001/HYG-002: no co-author trailers, no "generated with", ASCII-only comments).
  - Module boundaries (no cross-module model imports).

- [ ] **Step 3: Create `tool_guidance.md` [C4]**
Document exact CLI command syntax in `D:\Apps\AnD Monitoring System\.harness\tool_guidance.md`:
  - `php artisan test --parallel <single_path>` (and serial restriction).
  - `composer deptrac` in `backend/`.
  - `npm test` and `npm run lint` in `frontend/`.

- [ ] **Step 4: Create Experience Library files**
Initialize `D:\Apps\AnD Monitoring System\.harness\experience\INDEX.md` and domain sub-files (`backend/EXPERIENCE.md`, `frontend/EXPERIENCE.md`, `architecture/EXPERIENCE.md`) with the standard `### EXP-ID: Title` schema (Cue, Lesson, Evidence).

- [ ] **Step 5: Setup Junctions for C3, C5, C6, C8**
Run PowerShell script to create symbolic junctions in `.harness/` pointing to `.claude/`:
  - `.harness/sub_agents` -> `.claude/agents`
  - `.harness/skills` -> `.claude/skills`
  - `.harness/hooks` -> `.claude/hooks`
  - `.harness/LongTermMEMORY.md` -> `.claude/memory/MEMORY.md`

---

### Task 2: Refactor DAMS Root `AGENTS.md` & Add Sync Verifier

**Files:**
- Modify: `D:\Apps\AnD Monitoring System\AGENTS.md` (reduce from 945 lines to ~100 lines)
- Create: `D:\Apps\AnD Monitoring System\scripts\check-harness-sync.ps1`
- Test: `pwsh -NoProfile -File "D:\Apps\AnD Monitoring System\scripts\check-harness-sync.ps1"`

**Interfaces:**
- Consumes: `.harness/` files created in Task 1.
- Produces: Streamlined router `AGENTS.md` and an automated sync verification script.

- [ ] **Step 1: Draft the slim router `AGENTS.md`**
Replace the monolithic 945-line content of `D:\Apps\AnD Monitoring System\AGENTS.md` with:
  1. Role & brief instructions (executor role, report format).
  2. Critical guardrails alert block (worktrees, commit markers, hygiene).
  3. Technology stack table.
  4. Quick navigation table with markdown links to:
     - System Mission -> `.harness/systemprompt.md`
     - Full Guardrails -> `.harness/guardrails.md`
     - Tool Guidance -> `.harness/tool_guidance.md`
     - Backend Conventions -> `.claude/rules/backend-conventions.md`
     - Frontend Conventions -> `.claude/rules/frontend-conventions.md`
     - Skills Catalog -> `.harness/skills/`
     - Experience Library -> `.harness/experience/INDEX.md`

- [ ] **Step 2: Write `scripts/check-harness-sync.ps1`**
Create a PowerShell verification script that:
  1. Verifies all links in `AGENTS.md` exist and are accessible.
  2. Verifies `.harness/` junctions resolve cleanly.
  3. Scans `AGENTS.md` and `.harness/` for non-ASCII characters to prevent CI hygiene failures.
  4. Exits with 0 on success, 1 on failure with actionable error messages.

- [ ] **Step 3: Run verification script**
Run: `pwsh -NoProfile -File "D:\Apps\AnD Monitoring System\scripts\check-harness-sync.ps1"`
Expected: Output showing all checks passed with exit code 0.

---

### Task 3: Golden Tasks Fixture Scaffolding in `harness2`

**Files:**
- Create: `D:\Apps\harness2\dependencies\dams-tasks\backend-kpi-endpoint\task.json`
- Create: `D:\Apps\harness2\dependencies\dams-tasks\backend-kpi-endpoint\source\ParameterKpiController.php`
- Create: `D:\Apps\harness2\dependencies\dams-tasks\backend-kpi-endpoint\tests\ParameterKpiEndpointTest.php`
- Create: `D:\Apps\harness2\dependencies\dams-tasks\frontend-kpi-card\task.json`
- Create: `D:\Apps\harness2\dependencies\dams-tasks\frontend-kpi-card\source\KpiParameterCard.vue`
- Create: `D:\Apps\harness2\dependencies\dams-tasks\frontend-kpi-card\tests\KpiParameterCard.spec.ts`
- Create: `D:\Apps\harness2\dependencies\dams-tasks\deptrac-boundary-contract\task.json`
- Create: `D:\Apps\harness2\dependencies\dams-tasks\deptrac-boundary-contract\source\HelpdeskService.php`
- Create: `D:\Apps\harness2\dependencies\dams-tasks\deptrac-boundary-contract\deptrac.yaml`

**Interfaces:**
- Consumes: Task prompt, initial buggy/incomplete files, and verification criteria.
- Produces: Standalone, offline-testable task bundles for `DamsAdapter`.

- [ ] **Step 1: Scaffold Task 1 (`backend-kpi-endpoint`)**
Create task definition JSON with task prompt: "Implement calculation endpoint for KPI Parameter P-04 in Monitoring module with input validation", initial controller stub, and automated test expecting 200 OK with correct JSON schema.

- [ ] **Step 2: Scaffold Task 2 (`frontend-kpi-card`)**
Create task definition JSON with prompt: "Create Vue 3 + Vuetify 3 Materio KPI card component displaying status badge and threshold percentage", initial template stub, and Vitest test spec.

- [ ] **Step 3: Scaffold Task 3 (`deptrac-boundary-contract`)**
Create task definition JSON with prompt: "Resolve direct coupling violation in HelpdeskService importing Employee Entity by introducing an Interface Contract in Core", initial violating code, and Deptrac config.

---

### Task 4: Implement `DamsAdapter` in `harness2/benchmarks/dams.py`

**Files:**
- Create: `D:\Apps\harness2\harness2\benchmarks\dams.py`
- Create: `D:\Apps\harness2\tests\test_dams_adapter.py`

**Interfaces:**
- Consumes: `harness2.core.spec.BenchmarkAdapter`, `BenchSpec`, `BenchPrompts`, `ChannelSpec`.
- Produces: Fully functional `DamsAdapter` class.

- [ ] **Step 1: Write the failing unit test for `DamsAdapter`**
Write `tests/test_dams_adapter.py` testing:
  - Adapter instantiation and `spec.name == "dams"`.
  - `tasks()` returns `["backend-kpi-endpoint", "frontend-kpi-card", "deptrac-boundary-contract"]`.
  - `domain_of(task_id)` returns appropriate domain (`backend`, `frontend`, `architecture`).
  - `base_harness()` discovers DAMS `.harness` components.

- [ ] **Step 2: Run test to verify it fails**
Run: `python -m unittest tests/test_dams_adapter.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness2.benchmarks.dams'`.

- [ ] **Step 3: Implement `DamsAdapter` in `harness2/benchmarks/dams.py`**
Implement:
  - `__init__(self, root: Path, dams_root: Path | None = None)`
  - `tasks(self, split: str = "all") -> list[str]`
  - `domain_of(self, task_id: str) -> str`
  - `task_prompt(self, task_id: str) -> str`
  - `task_files(self, task_id: str) -> Path | None`
  - `base_harness(self) -> Path`
  - `execute(self, task_id, harness_dir, run_id, timeout)`
  - `delivered(self, run_dir: Path) -> bool`
  - `deliverable_text(self, run_dir: Path) -> str`
  - `trajectory(self, run_dir: Path, obs_trunc: int) -> str`
  - `score(self, run_dir: Path, task_id: str) -> dict | None`

- [ ] **Step 4: Run unit test to verify it passes**
Run: `python -m unittest tests/test_dams_adapter.py`
Expected: PASS.

---

### Task 5: Wire `DamsAdapter` into Harness² CLI Suite

**Files:**
- Modify: `D:\Apps\harness2\harness2\cli\setup.py`
- Modify: `D:\Apps\harness2\harness2\cli\check.py`
- Modify: `D:\Apps\harness2\harness2\cli\run_sweep.py`
- Modify: `D:\Apps\harness2\harness2\cli\_common.py` (if benchmark choices are enumerated)

**Interfaces:**
- Consumes: `DamsAdapter` from `harness2.benchmarks.dams`.
- Produces: CLI support for `--bench dams` across all Harness² commands.

- [ ] **Step 1: Register `dams` benchmark in `_common.py` or parser choices**
Add `"dams"` to benchmark choices in CLI argument parsers.

- [ ] **Step 2: Add setup routine for `dams` in `harness2/cli/setup.py`**
Implement setup step ensuring tasks directory and dependencies are checked.

- [ ] **Step 3: Register adapter resolver in `check.py` and `run_sweep.py`**
Map `"dams"` to instantiate `DamsAdapter`.

- [ ] **Step 4: Verify with offline `--dry-run` commands**
Run: `python -m harness2.cli.check --bench dams --dry-run`
Run: `python -m harness2.cli.run_sweep --bench dams --dry-run`
Expected: Prints task stream for domains `backend`, `frontend`, `architecture` with 0 errors.

---

### Task 6: End-to-End Regression & Verification

**Files:**
- Test: All tests in `tests/test_workflow.py` and `tests/test_dams_adapter.py`

- [ ] **Step 1: Run complete test suite**
Run: `python -m unittest discover tests`
Expected: All tests pass.

- [ ] **Step 2: Commit all changes in `harness2`**
Run: `git add .` and `git commit -m "feat: implement DamsAdapter and golden tasks for AnD Monitoring System"`
