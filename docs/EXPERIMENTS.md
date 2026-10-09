# Experiment guide

Complete [setup](SETUP.md) and choose a [benchmark](BENCHMARKS.md) first. This
guide covers recursion budgets, task selection, reproducibility, and evaluation.

## Recursion modes

Both modes start with `k` reference executions of the same base harness.

- **Parallel recursion** proposes and probes `k` general (invariant) candidates
  and `k` domain-specific candidates. Each of `k` independent compositions uses
  the full candidate pool and execution evidence to produce a refined harness.
- **Sequential recursion** runs `k` Propose–Probe–Compose steps, carrying the
  refined harness and accumulated execution evidence into the next step.

After adoption, reflection stores the adopted harness and evidence-supported
lessons in a per-domain library. Tasks within a domain run in order and read
only earlier entries; different domains can run concurrently. Libraries are
separated by benchmark, executor, mode, budget, and seed.

| Option | Behavior |
|---|---|
| `--k 1` | One Propose–Probe–Compose step, followed by reflection after adoption |
| `--k K --mode parallel` | Explore `K` candidates per scope and produce `K` compositions |
| `--k K --mode sequential` | Refine the harness through `K` successive steps |
| `--workers N` | Run `N` domains concurrently; tasks within each domain stay ordered |
| `--substrate cx` | Use Codex instead of the default OpenCode executor |
| `--seed N` | Shuffle the task stream reproducibly; does not control model sampling |

Use the same benchmark, executor, subset, `k`, mode, and seed for refinement,
judging, and reporting. For example:

```bash
export HARNESS2_RUNS_DIR=runs/lab-sequential-k3
harness2 --bench lab --k 3 --mode sequential \
  --only-domains antitrust-competition --max-tasks 1
harness2-judge --bench lab --k 3 --mode sequential
harness2-report --bench lab --k 3 --mode sequential
```

Use a separate output root when changing the budget or mode to keep experiments
easy to distinguish.

## Task selection and cost

Model-backed runs incur API costs for executor rollouts and improvement stages.
The full LAB task stream contains 1,143 tasks. Start with a small selection and
review model pricing before launching a full run.

- `--only-domains` restricts the run to the named domains.
- `--max-tasks` caps tasks **per domain**, not across the whole run.
- `--dry-run` previews selected tasks and available domains without model calls.

Each benchmark also has a script that runs checks, refinement, judging, and
reporting in sequence. Set `SUBSTRATE`, `K`, `MODE`, and, for WorkBuddy,
`SUBSET`; additional arguments are passed to the refinement command:

```bash
bash scripts/run_lab.sh --only-domains antitrust-competition --max-tasks 1
SUBSTRATE=cx K=3 MODE=sequential bash scripts/run_jobbench.sh --max-tasks 1
SUBSET=web bash scripts/run_workbuddy.sh --max-tasks 1
```

## Reproducibility

When comparing budgets or modes, keep task selection, executor, improver, judge,
and web access fixed. Use a distinct `HARNESS2_RUNS_DIR` when changing models or
task selection; the saved configuration prevents incompatible resumes.

JobBench's dataset is resolved at download time rather than pinned to a commit.
Setup records the downloaded Hugging Face revision in
`dependencies/job-bench-eval/dataset/main/.harness2-download.json`. Report that
revision alongside JobBench results.

### Task partitions

To create a reproducible, domain-stratified partition of the installed tasks:

```bash
python scripts/split_tasks.py --bench lab --seed 42 --output splits/lab.json
harness2 --bench lab --split-file splits/lab.json --split test --mode parallel
```

The default train fraction is 0.5. Generated partitions are local files, not
release assets, and the script refuses to overwrite an existing file.

## Baseline evaluation

`harness2-judge --bases` scores a run namespace's reference executions, writing
`base_judged.json` beside `judged.json` for the base-harness comparison.
`harness2-report` covers the refined harness only.

## Web access

All three benchmarks are public, and JobBench publishes each task's
`RUBRICS.json` on Hugging Face. An executor with network access can therefore
retrieve grading criteria. In a validation run of this release, a Codex rollout
on JobBench downloaded its task's rubric without being instructed to.

The release does not restrict network access. After setup, block the following
hosts for the executor to reduce access to benchmark data and grading criteria:

- `huggingface.co`, `hf.co`, and `*.hf.co`
- `github.com` and `raw.githubusercontent.com`, for benchmark repositories

Setup itself needs these hosts to download dependencies and data. Codex runs
with its sandbox disabled, so apply restrictions at the network level, such as
an egress firewall or DNS policy on the executor host, rather than relying on a
tool setting. Report the restrictions used alongside results.

## Results and resume

The default output roots are `runs/lab/`, `runs/jb/`, and
`runs/wb/<subset>/`. They contain task summaries, execution artifacts, adopted
harnesses, reflection notes, and graded results.

Repeat the same command to resume. Failed evaluations remain unscored rather
than being counted as zero. Use a new `HARNESS2_RUNS_DIR` when changing models or
task selection.

Refinement, judging, and reporting use `harness2`, `harness2-judge`, and
`harness2-report`; each supports `--help`.
