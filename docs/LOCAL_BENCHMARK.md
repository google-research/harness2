# Local Tasks Benchmark (`--bench local`)

Run Harness² on your own folder of tasks, instructions, and source documents without requiring JobBench, LAB, or WorkBuddy installations.

---

## 1. Directory Structure

Organize your tasks into a directory grouped by **domain** (practice area, occupation, or category):

```text
my-tasks/
├── bench.toml                 # (Optional) Benchmark-level descriptions
└── <domain>/                  # E.g. "legal", "dev", "accounting"
    └── <task_id>/             # E.g. "contract-review", "fix-bug-12"
        ├── task.md            # (Required) Natural language task instructions
        ├── files/             # (Optional) Input documents or code
        └── task.toml          # (Optional) Expected outputs and verification checks
```

---

## 2. Configuration (`task.toml` & `bench.toml`)

### Task Metadata (`task.toml`)
Place an optional `task.toml` inside any task directory:

```toml
# Expected deliverables that must be generated and non-empty:
outputs = ["dist/bundle.js", "report.md"]

# Post-selection verification command (run ONLY by harness2-judge):
check = ["pytest", "tests/"]

# Execution timeout in seconds (default: 3600):
timeout = 300
```

### Benchmark Metadata (`bench.toml`)
Place an optional `bench.toml` at the root of your tasks folder:

```toml
work = "General software engineering and documentation tasks."
domain_word = "practice"
deliverable_word = "solution"
```

---

## 3. Running Adaptations

### Preview task stream (`--dry-run`)
```bash
harness2 --bench local --tasks-dir ./my-tasks --dry-run
```

### Run adaptation sweep
```bash
# Run with OpenCode substrate
harness2 --bench local --tasks-dir ./my-tasks --substrate oc --k 1 --mode parallel

# Run with Codex substrate
harness2 --bench local --tasks-dir ./my-tasks --substrate cx --k 1 --mode parallel

# Use a custom starter harness
harness2 --bench local --tasks-dir ./my-tasks --base-harness ./my-seed-harness --k 1
```

### Post-selection verification & judging
```bash
harness2-judge --bench local --tasks-dir ./my-tasks --k 1 --modes parallel
```
* If a task defines a `check` command in `task.toml`, it is executed in the final workspace.
* If no `check` is configured, the task is marked as `ungraded (not configured)`, preserving answer-free discipline.

### Inspect results
```bash
harness2-report --bench local --tasks-dir ./my-tasks --k 1
```

---

## 4. Architectural Principles

1. **Clean Workspace Isolation:** Each rollout receives an isolated workspace directory with a fresh copy of `files/`. Modifications made in rollout #1 never bleed into rollout #2.
2. **Answer-Free Discipline:** Proposers and selectors are never exposed to private verification checks or grader scores. Checks run only after selection commits.
3. **Resume Safety:** Tasks are content-fingerprinted across prompt text and source files. Editing `task.md` or files inside `files/` invalidates cached rollouts and safely re-executes.
4. **Dependency-Free:** Metadata parsing uses Python 3.12's standard library `tomllib` without requiring external YAML dependencies.
