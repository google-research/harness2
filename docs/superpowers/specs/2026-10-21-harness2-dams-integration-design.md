# Design Document: Harness² Integration for AnD Monitoring System & AD-Strategic-Blueprint

- **Date:** 2026-10-21
- **Status:** Approved
- **Target Projects:**
  - Primary: `D:\Apps\AnD Monitoring System` (DAMS / EO-PIMS)
  - Secondary: `D:\Apps\AD-Strategic-Blueprint`
  - Engine: `D:\Apps\harness2`
- **Authors:** Santo Wijaya & Antigravity

---

## 1. Executive Summary & Problem Statement

### 1.1 Context
`AnD Monitoring System` (DAMS / EO-PIMS) is a mission-critical internal platform (Laravel 12 backend with modular architecture, Vue 3 + Vuetify frontend, PostgreSQL, and LDAP auth) serving the A&D Department at PT Ecogreen Oleochemicals.

### 1.2 Problems with Current Setup
1. **Monolithic `AGENTS.md` Clutter:**
   The root `AGENTS.md` in DAMS has grown to **50,925 bytes (~945 lines)**. Because it is injected in full on every agent turn, it consumes ~15,000–20,000 prompt tokens per interaction, regardless of whether the agent is doing a 1-line typo fix, a backend query, or a frontend component.
2. **Maintenance Duplication & Drift:**
   Claude Code reads `.claude/rules/` (`worktree.md`, `backend-conventions.md`, `frontend-conventions.md`), while OpenCode / Antigravity / Codex do not automatically parse `.claude/rules/`. As a workaround, rules were manually copy-pasted into `AGENTS.md`, leading to frequent configuration drift.
3. **Absence of Systematic Agent Evaluation & Experience Retention:**
   There is currently no automated test harness to measure whether agent prompt/skill changes improve or degrade code generation accuracy across DAMS tasks, nor a structured cross-task experience accumulation mechanism.

### 1.3 Solution
Implement a two-stage integration using the **Harness²** framework:
- **Scenario B (Modularization & Experience Library):** Refactor DAMS agent configuration into the 8-component Harness² tree (C1–C8) using a **Single Source of Truth** pattern linked to `.claude/`, while slimming root `AGENTS.md` into an active router (~100 lines). Establish an on-disk Experience Library (`.harness/experience/`).
- **Scenario A (Automated Benchmarking & Self-Refinement):** Build a dedicated `DamsAdapter` in `harness2` with 3 representative Golden Tasks (Backend API + Unit Test, Frontend Vue3 Component, Deptrac Modular Boundary Fix). Run Propose–Probe–Compose optimization cycles via CLI.
- **Phase 2 Replication:** Replicate this validated design pattern to `AD-Strategic-Blueprint`.

---

## 2. Architecture Overview

### 2.1 The 8 Harness Components (C1–C8)
Harness² models an agent's harness as a tree of 8 editable components:

```
[Harness Tree (c1..c8)]
├── C1: systemprompt.md             (Core role, product mission, high-level architecture)
├── C2: guardrails.md               (Strict invariant rules: isolated worktree, commit markers, hygiene)
├── C3: LongTermMEMORY.md           (Persistent lessons, past bug resolution memory)
├── C4: tool_guidance.md            (Precise CLI invocation rules: paratest, vitest, deptrac)
├── C5: sub_agents/                 (Delegated specialist definitions: api-reviewer, qa-guardian)
├── C6: skills/                     (On-demand execution SOPs: dams-backend, dams-frontend, dams-deptrac)
├── C7: scripts/                    (Deterministic helper scripts: hygiene check, worktree verifier)
└── C8: hooks/                      (PreToolUse blocking enforcement scripts in .claude/hooks/)
```

### 2.2 Dual-Environment Coexistence (Antigravity & Claude Code)
The integration maintains 100% backward compatibility with existing Claude Code hooks and settings:

```
                          D:\Apps\AnD Monitoring System
                                        │
           ┌────────────────────────────┴────────────────────────────┐
           ▼                                                         ▼
    [.claude/ Directory]                                      [.harness/ Directory]
    (Master Source of Truth)                                  (Harness² Component Tree)
    ├── rules/ (backend/frontend rules)                       ├── systemprompt.md [C1]
    ├── agents/ (subagent markdown specs) <──────Junction─────┼── sub_agents/ [C5]
    ├── skills/ (skills catalog)          <──────Junction─────┼── skills/ [C6]
    ├── hooks/ (PreToolUse .ps1 scripts)  <──────Junction─────┼── hooks/ [C8]
    ├── memory/ (historical lessons)      <──────Junction─────┼── LongTermMEMORY.md [C3]
    ├── CLAUDE.md & settings.json                             ├── guardrails.md [C2]
                                                              ├── tool_guidance.md [C4]
                                                              └── experience/ (INDEX.md, lessons)
                                                                             ▲
                                                                             │
                                    [AGENTS.md] (Root Router ~100 lines) ─────┘
                                    - Lean mission summary
                                    - Markdown links to .harness/ components
                                    - Consumed by Antigravity & Codex CLI
```

---

## 3. Scenario B Implementation: `AnD Monitoring System`

### 3.1 Root `AGENTS.md` Slim Router
The root `AGENTS.md` is reduced from 945 lines to ~100 lines. It acts as an index table:
- **Mission:** Executor identity for DAMS (EO-PIMS).
- **Critical Guardrails Summary:** Immediate warning regarding isolated worktree requirement (`.worktrees/<slug>`), mandatory `[ci] [cd]` commit markers, and hygiene rules (no AI trailers, ASCII-only comments). Full details link to `.harness/guardrails.md`.
- **Component Links:**
  - Backend Dev: Links to `.claude/rules/backend-conventions.md` and `.harness/skills/dams-backend.md`.
  - Frontend Dev: Links to `.claude/rules/frontend-conventions.md` and `.harness/skills/dams-frontend.md`.
  - Commands & Tool Guidance: Links to `.harness/tool_guidance.md`.
  - Experience Log: Links to `.harness/experience/INDEX.md`.

### 3.2 Single Source of Truth via Junctions / Symbolic Pointers
To eliminate manual copy-paste:
- `.harness/sub_agents` -> points to `.claude/agents`
- `.harness/skills` -> points to `.claude/skills`
- `.harness/hooks` -> points to `.claude/hooks`
- `.harness/LongTermMEMORY.md` -> mirrors or points to `.claude/memory/MEMORY.md`

### 3.3 Experience Library Structure
Located at `D:\Apps\AnD Monitoring System\.harness\experience/`:
- `INDEX.md`: Master catalog of accumulated lessons learned across tasks, sorted by domain (`backend`, `frontend`, `architecture`, `devops`).
- `backend/EXPERIENCE.md`: Lessons on Laravel 12, PostgreSQL migrations, queries, and module dependencies.
- `frontend/EXPERIENCE.md`: Lessons on Vue 3, Vuetify Materio components, Pinia stores, and Axios endpoints.
- `architecture/EXPERIENCE.md`: Lessons on Deptrac boundaries, interfaces, and DTO contracts.

Format for each entry:
```markdown
### EXP-20261021-01: Inter-module coupling prevention
- **Cue:** When adding relationships between two Laravel modules (e.g., Helpdesk and Employee).
- **Lesson:** Never import the Eloquent Model directly across modules. Define an Interface in `Modules/Core/Contracts` and resolve it via Laravel's Service Container.
- **Evidence:** Avoids Deptrac MOD-003 architecture gate failure on CI.
```

### 3.4 Verification Script (`scripts/check-harness-sync.ps1`)
A lightweight PowerShell script in DAMS to verify:
1. All links in `AGENTS.md` resolve to existing files.
2. Junctions in `.harness/` are valid and reachable.
3. No non-ASCII characters exist in markdown guidelines that could fail hygiene CI gates.

---

## 4. Scenario A Implementation: `DamsAdapter` in Harness²

### 4.1 Adapter Seam (`harness2/benchmarks/dams.py`)
Implements the `BenchmarkAdapter` protocol from [`harness2/core/spec.py`](file:///D:/Apps/harness2/harness2/core/spec.py):

```python
class DamsAdapter:
    def __init__(self, root: Path):
        self.root = root  # Root of harness2 or isolated runner workspace
        self.dams_root = Path("D:/Apps/AnD Monitoring System")
        self.substrate = OpencodeSubstrate()
        self.spec = BenchSpec(
            name="dams",
            runs_root=self.root / "runs" / "dams",
            install_mode="workspace_config",
            prompts=BenchPrompts(
                work="implement and fix modular Laravel 12 and Vue 3 features in AnD Monitoring System",
                domain_word="domain",
                inv_levers="guardrails, tool guidance, scripts",
                dom_levers="skills, subagents, domain memory",
                yardstick="automated unit tests, deptrac architecture boundary, and lint checks",
            ),
            channels={
                "proposer": resolve_channel_model(None, None),
                "selector": resolve_channel_model(None, None),
            },
        )
```

### 4.2 The 3 Golden Tasks Definition

Tasks are stored in `dependencies/dams-tasks/<task-id>/`:

#### Task 1: `backend-kpi-endpoint`
- **Domain:** `backend`
- **Objective:** Create a new API Controller endpoint for KPI Parameter P-04 calculation in `Modules/Monitoring`, with FormRequest validation and a service method.
- **Fixture:** Isolated submodule workspace with Laravel test runner stub.
- **Execution:** Agent runs with candidate harness.
- **Success Criteria (`score()`):** Running `php artisan test --filter=ParameterKpiEndpointTest` passes with 100% assertions, 0 errors.

#### Task 2: `frontend-kpi-card`
- **Domain:** `frontend`
- **Objective:** Create a Vue 3 + Vuetify 3.10 component (`KpiParameterCard.vue`) displaying threshold status (Critical, Warning, Normal) with Materio design tokens and TypeScript typed props.
- **Fixture:** Isolated frontend component workspace with Vitest mock.
- **Success Criteria (`score()`):** Vitest component render test passes + ESLint returns 0 errors.

#### Task 3: `deptrac-boundary-contract`
- **Domain:** `architecture`
- **Objective:** A code snippet in `Modules/Helpdesk` directly instantiates `Modules/Employee/Entities/Employee`. Agent must refactor it to depend on `EmployeeContractInterface` in `Modules/Core`.
- **Fixture:** Minimal multi-module Laravel structure with Deptrac configuration.
- **Success Criteria (`score()`):** Running `composer deptrac` returns 0 violations.

### 4.3 CLI Integration
Registered in Harness² CLI suite:
- `harness2-setup dams`: Prepares fixtures and checks dependencies.
- `harness2-check --bench dams`: Validates paths, models, and sandboxes.
- `harness2 --bench dams --k 1 --mode parallel`: Runs adaptation sweep.
- `harness2-judge --bench dams --k 1 --mode parallel`: Scores and grades deliverables.
- `harness2-report --bench dams --k 1 --mode parallel`: Summarizes pass rates and cost.

---

## 5. Phase 2: Replication to `AD-Strategic-Blueprint`

Once DAMS integration is validated, apply the identical pattern to `AD-Strategic-Blueprint`:
1. **Scenario B in Blueprint:**
   - Slim `AGENTS.md` (currently 17KB) into a router.
   - Separate guidelines into `.harness/skills/ad-wi-authoring.md` and `.harness/skills/ad-doc-integrity.md`.
   - Maintain strict alignment with the repo's existing continuous improvement rule (`CI-YYYYMMDD-NN`).
   - Register deterministic scripts (`scripts/check_doc_integrity.py`, `scripts/verify_plantuml.py`) as C7 scripts.
2. **Scenario A in Blueprint (`BlueprintAdapter`):**
   - Golden Tasks:
     - `wi-authoring-standard`: Writing a new WI complying with RACI, version headers, and format.
     - `doc-integrity-fix`: Resolving broken cross-citations and missing links.
     - `plantuml-sync`: Generating valid PlantUML diagrams and syncing VitePress portal references.
   - Verification: `python scripts/check_doc_integrity.py` exit code 0.

---

## 6. Safety, Rollback, & Hygiene

1. **Git Isolation:**
   - In DAMS, all changes will be committed to a dedicated feature branch (`feat/harness2-modular-architecture`).
   - Root checkout on master is never modified directly.
2. **Commit Hygiene Gate Enforcement:**
   - All commits in DAMS must strictly include `[ci] [cd]` markers.
   - No AI co-author trailers or non-ASCII characters (complying with HYG-001 / HYG-002).
3. **Rollback Strategy:**
   - If any Claude Code workflow experiences unexpected disruption, reverting the commit restores the exact original `AGENTS.md` immediately.

---

## 7. Spec Self-Review Check

- **Placeholders:** None. All file paths, commands, and interfaces are fully qualified.
- **Contradictions:** Resolved. Claude Code's native `.claude/` files remain untouched as the master source; Harness² and Antigravity reference them via junctions and clean router links.
- **Ambiguity:** Golden tasks have explicit measurable scoring criteria (`php artisan test`, `vitest`, `deptrac`).
- **Scope:** Clearly bounded to DAMS first, with Phase 2 Blueprint replication following the same pattern.
