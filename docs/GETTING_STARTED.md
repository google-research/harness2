# Getting started: improve a recurring task

Suppose you prepare a weekly total from a transaction file. An agent keeps
forgetting refunds. You can fix the script directly, which is the right choice
for this small arithmetic problem. Here we use that understandable mistake to
see how Harness² tries changes, records executions, and carries lessons into
later improvement steps.

For a real project, consider Harness² when you have repeated tasks, outputs you
can assess, and a reason to compare changes to prompts, tools, or scripts.
Examples include document-based reports, financial work products, and coding
tasks. Start with a normal coding agent when you need one deliverable, or when
a known script or test already solves the problem.

## Choose an outcome

These are suggested reading and hands-on budgets, not completion guarantees.
Installation, downloads, model latency, quotas, and evaluation add time. You
can stop after any stage with something useful to inspect.

| Budget | Outcome | Execution |
|---|---|---|
| [5 minutes](#5-minutes-see-a-complete-local-run) | Explain what changed in a run | Existing local demo, after package install |
| [20 minutes](#20-minutes-change-the-input-and-check-the-answer) | Change a ledger and check saved answers | Local Python and scripted edits |
| [60 minutes](#60-minutes-compare-budgets-and-modes) | Compare artifacts and execution budgets | Local Python and scripted edits |
| [2 hours](#2-hours-prepare-one-model-backed-task) | Prepare, and if configured run, one benchmark task | Separate setup and paid model APIs |
| [1 day](#1-day-build-a-small-workflow-pilot) | Build a local task stream and define a real pilot | Runnable offline adapter; model integration is additional work |

The local exercises reuse [`harness2-demo`](../harness2/cli/demo.py),
[`DemoAdapter` and `DemoProposer`](../harness2/demo.py). Python really executes
the proposed scripts. The editor makes a predetermined fix; when selection is
needed it computes the exact sum. This demonstrates the workflow and its
artifacts, not model learning or measured improvement on unseen work.

## Install once

Use a source checkout, Python 3.12, Git, and
[uv](https://docs.astral.sh/uv/getting-started/installation/). Linux is the
documented platform for benchmarks. The offline exercises also run on macOS;
they use Python's standard library and do not need Node, Docker, cloud access,
or model credentials. Package installation may download build tools.

If you already have this repository, start at `uv venv` in its root:

```bash
git clone https://github.com/google-research/harness2.git
cd harness2
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
uv run --no-sync harness2-demo --help
```

Use the repository root and the same shell for all exercises. `--no-sync`
uses the environment you just installed without creating a lockfile. Define a
short name for the example script:

```bash
EXAMPLE=examples/getting_started/ledger.py
```

If a command cannot import `harness2`, repeat the editable install above and
check that you are in the repository root. The helper script is part of the
source tree, not the installed wheel. No exercise starts a server.

## 5 minutes: see a complete local run

**Outcome:** follow one mistake from the original script to a saved answer.
Prerequisite: the package install above. This stage is reading plus local
execution; no model runs.

```bash
uv run --no-sync harness2-demo --output runs/start --k 1
uv run --no-sync python "$EXAMPLE" inspect runs/start
```

Expect `2 tasks completed; 0 failed` and four `PASS` rows, one per task and
mode. The inspector reads the CSVs and actual saved `answer.txt` files:

| Task | Original total | Selected total | Sum of source amounts |
|---|---|---|---|
| ledger-1 | 105.25 | 85.15 | 100.25 - 20.10 + 5.00 |
| ledger-2 | 35.50 | 20.25 | 35.50 - 10.25 - 5.00 |

The base script filters out negative amounts. The scripted editor removes
that filter. Harness² executes the edited script and records the result.
At `k=1` there is only one composed final per mode, so adoption needs no
selector session. A reflection saves the known lesson about signed amounts.

**Try a change:** run only sequential mode in a fresh output directory:

```bash
uv run --no-sync harness2-demo --output runs/one-mode \
  --k 1 --mode sequential
uv run --no-sync python "$EXAMPLE" inspect runs/one-mode
```

Expect two `PASS` rows with the same totals. `k` controls the number of
refinement steps/candidates, not the number of tasks.

If the demo fails, inspect the error and the task's `summary.json` path printed
by the helper. Repeat the original demo command to resume unchanged inputs.
The demo constructor rewrites its built-in source CSVs, so use the next stage
to customize inputs. After inspecting them, `rm -r runs/one-mode` removes only
this stage's optional run. Keep `runs/start` for the next exercise.

## 20 minutes: change the input and check the answer

**Outcome:** reproduce the mistake with your own amounts and verify the fix.
Prerequisite: the first run. This is a local input change, not an agent prompt.

Inspect the first run's original script, saved summary, and lesson:

```bash
cat runs/start/base/scripts/summarize.py
uv run --no-sync python -m json.tool \
  runs/start/runs/demo-oc-agg1/ledger-1/summary.json
cat runs/start/library/k1/agg/accounting/harnesses/ledger-1/\
EXPERIENCE.md
```

`agg` means parallel and `seq` means sequential on disk. Summaries for both
modes live under `demo-oc-agg1`; `selections` points to each mode's actual
chosen execution. `oc` here names the demo's harness format. The demo does not
launch an OpenCode model.

Copy the two sample weeks and append a refund to the first one:

```bash
mkdir -p runs/my-ledgers
cp examples/getting_started/ledgers/*.csv runs/my-ledgers/
printf '%s\n' '-7.25' >> runs/my-ledgers/week-1.csv
uv run --no-sync python "$EXAMPLE" run \
  --input-dir runs/my-ledgers --output runs/custom --k 1
uv run --no-sync python "$EXAMPLE" inspect runs/custom
```

The selected first total is now **77.90**; the original still reports 105.25.
The second selected total stays 20.25. The script prints which input became
`ledger-1`, `ledger-2`, and so on, in sorted filename order. Each CSV must have
one `amount` column and at least one finite number with at most two decimal
places. There is no currency conversion or accounting validation.

The helper uses `LedgerAdapter(DemoAdapter)` to replace the task inputs and
task list, then calls the real `run_domain` function with `DemoProposer`. It
refuses an existing output directory, so changed inputs cannot silently reuse
an old result. For another change, use `runs/custom-v2`. Invalid CSVs fail
before creating the output directory.

If inspection reports `FAIL`, compare the printed expected and selected
totals, then open the answer and summary paths. A successful exit from a run
alone is not evidence that an answer is correct. The inspector returns a
nonzero status for wrong or missing answers. After the comparison stage you
can remove `runs/custom` and `runs/my-ledgers` with `rm -r`.

Next, keep the inputs fixed and change the refinement budget.

## 60 minutes: compare budgets and modes

**Outcome:** explain what extra execution buys in this example, and what it
does not establish. Prerequisite: the modified CSVs and `runs/custom` above.
Allow time to read artifacts; the local scripts need much less than an hour.

**Change:** keep the same inputs and increase `k` from 1 to 2:

```bash
uv run --no-sync python "$EXAMPLE" run \
  --input-dir runs/my-ledgers --output runs/compare-k2 --k 2
uv run --no-sync python "$EXAMPLE" inspect \
  runs/custom runs/compare-k2
```

Expect eight `PASS` rows. Both budgets return 77.90 and 20.25 in both modes.
Per task and mode, the ledger changes from `R=4 P=3` to `R=8 P=6`:

- `R` counts reference, candidate, and final executions.
- `P` counts general edits, task-domain edits, and compositions.
- Selection, reflection, and retries are outside those budget identities.
  Both modes share their base executions, so adding their `R` values would
  double-count those references. `demo.json` records actual local executions.

Parallel mode explores candidates from the same starting harness and composes
from their evidence. Sequential mode carries the refined harness and evidence
through successive steps. Here the known fix is already enough at `k=1`;
more executions do not change the answer. This example cannot tell you which
mode is better for a model or another task.

Inspect the retained script and the first task's lesson supplied to the next
task's improvement context:

```bash
cat runs/compare-k2/library/k2/agg/accounting/harnesses/\
ledger-1/harness/scripts/summarize.py
cat runs/compare-k2/runs/demo-oc-seq2/ledger-2/context/inv1/\
library/harnesses/ledger-1/EXPERIENCE.md
uv run --no-sync python -m json.tool runs/compare-k2/demo.json
```

The library supplies prior same-domain harnesses and lessons to later
improvement steps. It does not deploy them into another coding agent.
The scripted editor ignores those lessons; seeing a library entry proves the
context was carried, not that it improved a model's next answer.

Missing summaries or answers mean the comparison is incomplete. Recreate that
run under a new output name; do not fill gaps with expected values. Remove
`runs/compare-k2` when finished. Next, decide whether to prepare a real task
or continue directly to the offline pilot below.

## 2 hours: prepare one model-backed task

**Outcome:** choose one relevant task and define what evidence you will
inspect. A completed, judged run is possible only after setup and model access
work. Two hours is a work session, not a promise about installation or APIs.

The command-line runner currently supports JobBench (`jb`), LAB (`lab`), and
WorkBuddy-Bench (`wb`). For the ledger story, JobBench's professional work is a
reasonable place to look for related tasks. Read its installed task prompts
before choosing; do not assume it contains this exact CSV exercise.

Prerequisites for this stage: Linux, Python 3.12, Node.js 20+, Google Cloud CLI,
benchmark dependencies, and accessible executor, improver, and judge models.
See [setup](SETUP.md) and [benchmark requirements and licenses](BENCHMARKS.md).
The documented Vertex configuration incurs model API costs. The optional
Codex executor requires a Responses API endpoint and credentials; a Codex
subscription login alone does not configure it. The offline examples need
none of these credentials.

Preview setup without downloads, credential checks, or model calls:

```bash
uv run --no-sync harness2-setup jb --dry-run
```

Expected evidence: commands describing pinned dependencies and task-data
installation, ending in `Setup plan complete.` If you have no model API
access or budget, stop here and continue to the one-day offline pilot. A setup
plan is not an installed benchmark or a model result.

After following [SETUP.md](SETUP.md) to install and authenticate, use:

```bash
uv pip install --python .venv/bin/python -e '.[render]'
uv run --no-sync harness2-setup jb
uv run --no-sync harness2-check --bench jb
uv run --no-sync harness2 --bench jb --k 1 --mode parallel \
  --max-tasks 1 --dry-run
```

The last command lists up to one task **per domain**, without execution.
Choose one exact domain name from that output, then preview just that domain:

```bash
DOMAIN='replace-with-an-installed-domain-name'
export HARNESS2_RUNS_DIR=runs/first-jb
uv run --no-sync harness2 --bench jb --k 1 --mode parallel \
  --only-domains "$DOMAIN" --max-tasks 1 --dry-run
```

Proceed only when the preview says **1 task over 1 domain**, model access is
configured, and you have set an acceptable spending budget with your provider.
`--k` is not a dollar cap: one task at `k=1` uses four executor rollouts and
three improvement sessions, plus reflection and judging. Higher budgets can
also require selection; retries add calls. Apply the documented
[executor network restrictions](EXPERIMENTS.md#web-access) so public answers
cannot contaminate your comparison. Codex runs with its sandbox disabled.

The following commands cross the paid execution boundary:

```bash
uv run --no-sync harness2 --bench jb --k 1 --mode parallel \
  --only-domains "$DOMAIN" --max-tasks 1
uv run --no-sync harness2-judge --bench jb --k 1 \
  --mode parallel
uv run --no-sync harness2-judge --bench jb --k 1 \
  --mode parallel --bases
uv run --no-sync harness2-report --bench jb --k 1 \
  --mode parallel
```

Inspect the deliverable and `summary.json`, then `judged.json` and
`base_judged.json` under `runs/first-jb/jb/adapt/jb-oc-agg1/`.
The report covers refined results; baseline scores are in `base_judged.json`.
Record the task ID, models, dataset revision, costs, and network restrictions.
A missing/failed judgment stays unscored. One task is a setup check, not a
general quality claim.

**Try a change:** preview sequential mode with a new output root, keeping
the domain, task cap, models, and budget fixed:

```bash
export HARNESS2_RUNS_DIR=runs/first-jb-sequential
uv run --no-sync harness2 --bench jb --k 1 --mode sequential \
  --only-domains "$DOMAIN" --max-tasks 1 --dry-run
```

Run and judge that comparison only if the first result justifies more spend.
Use matching mode and `k` on all commands; see [experiments](EXPERIMENTS.md).

For unavailable dependencies or credentials, return to setup. Checks do not
prove model availability; a model may fail on its first call. For transient
failures, repeat the same run or judge command to resume. Use a new root when
changing models or task selection. Stop foreground commands with Ctrl-C;
inspect any surviving executor processes before leaving. This path starts no
bridge or Docker service. Keep evidence until reviewed, then remove only
`runs/first-jb` if desired and run `unset HARNESS2_RUNS_DIR`.

## 1 day: build a small workflow pilot

**Outcome:** turn a recurring job into explicit inputs, outputs, and checks.
Prerequisites: the local exercises and basic Python. Model integration is a
separate engineering task; you can complete this pilot offline.

Start with three synthetic weeks: a refund, an all-positive control, and a
zero-net week. This command creates a new input directory and refuses to
overwrite one that already exists:

```bash
uv run --no-sync python - <<'PY'
from pathlib import Path
root = Path('runs/pilot-inputs')
root.mkdir(parents=True, exist_ok=False)
weeks = ('120.00\n-20.00\n', '45.00\n5.00\n', '18.00\n-18.00\n')
for index, amounts in enumerate(weeks, 1):
    (root / f'week-{index}.csv').write_text('amount\n' + amounts)
PY
uv run --no-sync python "$EXAMPLE" run \
  --input-dir runs/pilot-inputs --output runs/pilot --k 1
uv run --no-sync python "$EXAMPLE" inspect runs/pilot
```

Expect six `PASS` rows. Selected totals are **100.00, 50.00, and 0.00**.
Original totals are 120.00, 50.00, and 18.00. The positive-only control shows
that an edited harness can produce the same result as the original.

**Modify one case:** append `-1.00` to `week-2.csv`, use a fresh output root,
and predict the new total before running:

```bash
printf '%s\n' '-1.00' >> runs/pilot-inputs/week-2.csv
uv run --no-sync python "$EXAMPLE" run \
  --input-dir runs/pilot-inputs --output runs/pilot-v2 --k 1
uv run --no-sync python "$EXAMPLE" inspect runs/pilot-v2
```

The second selected total becomes **49.00**. The first and third stay fixed.
Read [`LedgerAdapter`](../examples/getting_started/ledger.py) beside the
[`BenchmarkAdapter` protocol](../harness2/core/spec.py). The example changes
`tasks()` and materializes CSVs; inherited methods define the prompt, execute
the script, and expose deliverables and trajectories. `run_domain` threads the
library through the task stream. There is no generic `harness2 --task` flag.

For a different job, implement that protocol: list tasks and domains, expose
only task source material, supply a base harness, execute one task, and return
the resulting artifacts and readable trajectory. Use the existing benchmark
adapters as references for connecting a real executor. Replacing only the
CSV inputs or the proposer does not turn this local Python executor into a
general coding agent.

Before investing in that integration, write down:

- The recurring job and a simpler baseline, such as your existing agent with
  a fixed prompt or a deterministic script.
- A few representative inputs, including a case that already works and a
  failure case. Reserve separate unseen inputs for later assessment.
- Required artifacts and an independent check. Keep reference answers and
  grading criteria outside improvement context; the demo's scripted selector
  is for teaching and is not a template for blind model evaluation.
- A model/runtime/spending budget and a decision rule: what improvement would
  make the extra executions worthwhile, including regressions and failures?

Use synthetic data while building the adapter. A real pilot should hold task
selection and model settings fixed when comparing harnesses, inspect actual
outputs, and record unscored tasks separately. Review any useful adopted
prompt or script before integrating it into another application.

If your new job no longer fits the signed-sum contract, stop extending
`DemoProposer`; implement and test the new adapter first. For the local pilot,
invalid data or existing outputs produce an error before execution. Inspect
the results, then remove `runs/pilot`, `runs/pilot-v2`, and `runs/pilot-inputs`
with `rm -r`. Keep a record of your pilot decision before expanding scope.
