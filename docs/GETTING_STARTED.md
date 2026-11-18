# Getting started by outcome

A path from a first local run to a bounded experiment with your own work. Every
stage says what you will have at the end, what it costs, and how to undo it.

## First: when you want Harness² at all

Harness² optimizes the machinery around a model — prompts, guardrails, memory,
skills, scripts, subagents, plugins — from execution evidence. Reach for it when
you have a **recurring class of tasks** and want the harness to keep getting
better at them:

- A **one-off task** ("fix this bug", "draft this one memo")? Use a normal
  coding agent. There is nothing to improve *across* yet.
- A **repeated task whose shape you fully know** (same input, same output
  format, checkable)? A deterministic script beats an agent and beats this
  framework.
- A **recurring, evolving class of tasks** where the instructions, tool
  guidance, and skills that worked last time should inform next time? That is
  the case Harness² is built for.

Two facts that shape everything below:

- **Stored lessons feed future Harness² steps, not other agents.** The
  improvement library is read by later Propose–Probe–Compose steps in the same
  run tree. Nothing is auto-applied to a separate production agent; promoting a
  validated harness is a decision you make from the saved evidence.
- **The offline stages are scripted; the machinery is real.** In the demo and
  the ledger example, the editor and the selector are deterministic scripts, so
  stages run in seconds with no credentials and no model-quality claim. The
  workflow around them — probing, composition, blind selection, the library,
  resume — is the production code path.

## Three budgets, kept separate

| Budget | Stages 1–3 | Stage 4 | Stage 5 |
|---|---|---|---|
| Your reading time | ~15 min each | ~45 min | open-ended |
| Machine time | seconds | minutes–hours (task-dependent) | your tasks |
| Money | **$0** — no credentials | **paid API calls** start here | your tasks |

Installation (once, before Stage 1): a source checkout,
[uv](https://docs.astral.sh/uv/getting-started/installation/), Python 3.12:

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e '.[render]'
```

---

## Stage 1 — Understand one local run

**End state:** you can name every stage of one Propose–Probe–Compose step and
point to the file each one wrote. **Cost:** ~15 min, seconds of compute, $0.

Run the built-in demo (two small ledger tasks, deterministic editor):

```bash
harness2-demo --output runs/demo --k 1
```

Expected evidence — the run's own accounting:

- `runs/demo/runs/demo-oc-agg1/ledger-1/summary.json` — the task's record.
  `modes.agg.ledger` shows the budget identity: `R = 4` rollouts
  (`base1, inv1, dom1, agg1`) and `P = 3` proposer sessions at k=1.
- `modes.agg.stages[]` — each improvement stage: what it changed, whether it
  fell back, what it cost (`usd: 0.0` — scripted).
- `selections.agg` — which candidate blind selection picked and why.
- `runs/demo/library/k1/agg/accounting/harnesses/ledger-1/EXPERIENCE.md` —
  the library entry task 1 leaves for task 2: cue, lesson, evidence,
  confidence. Task 2's proposer reads it (see
  `runs/.../ledger-2/context/inv1/library/...`).

**A meaningful modification:** open `ledger-2/summary.json` and compare its
`context/*/library/` paths against `ledger-1`'s — the second task starts with
the first task's entry on disk. That is harnessing-level recursion, in files.

**If something looks wrong / cleanup:** delete `runs/demo` — the demo writes
only there. A corrupt summary is re-derived by re-running (it prints
`re-running` and rebuilds).

**Next:** Stage 2 puts *your* numbers through the same machinery.

## Stage 2 — Change its inputs

**End state:** one run driven by your own data, and proof that changed inputs
re-run rather than reuse stale answers. **Cost:** ~15 min, seconds, $0.

Use the [ledger example](../examples/getting_started/README.md):

```bash
mkdir my-ledgers && printf 'amount\n1200.00\n-450.50\n33.25\n' > my-ledgers/week-1.csv
python examples/getting_started/ledger_run.py --inputs my-ledgers --output runs/week-1
```

Expected evidence: inspection lines like
`week-1  agg PASS expected Total: 782.75  saved 'Total: 782.75'`, and the
`summary.json`/library tree from Stage 1 — now for your task. Selection here is
*exact*: the scripted selector picks the candidate whose total equals the
signed sum of your CSV, independent of the editor.

**A meaningful modification:** append a line to `my-ledgers/week-1.csv` and
re-run the same command. Watch the log: `task inputs … != … -- re-running`,
only that task executes again, and the new total appears. The task's
fingerprint covers its source files, so a stale answer can never be served
silently.

**If something looks wrong / cleanup:** `--inspect-only` re-checks saved
answers without running (tamper any `answer.txt` and it exits 1); delete
`runs/week-1` to start over. The script writes nothing outside `--output`.

**Next:** Stage 3 varies the *method's* dials on the same inputs.

## Stage 3 — Compare budgets and modes

**End state:** you can read a run at any `--k` and know what parallel vs
sequential bought. **Cost:** ~15 min, under a minute of compute, $0.

```bash
python examples/getting_started/ledger_run.py --inputs my-ledgers --output runs/k1  # k=1
harness2-demo --output runs/demo-k2 --k 2
```

(The example pins k=1; `harness2-demo` exposes both modes at any k.)

Expected evidence:

- Budget identity scales with k: at k=2,
  `demo-oc-agg2/ledger-1/summary.json` shows `R = 8`, `P = 6`, and namespaces
  appear for **both modes** — `demo-oc-agg2/` (parallel) and `demo-oc-seq2/`
  (sequential).
- Parallel probes more candidates then composes from shared evidence;
  sequential refines through k successive steps
  ([README](../README.md#experiments)).
- At **k=1 the two modes are the same procedure** — one invariant draw, one
  domain draw, one composition. Any gap between them at k=1 is a noise floor,
  not a result (`harness2-report` prints this note).

**A meaningful modification:** in the k=2 summary, list
`modes.agg.stages[].label` vs `modes.seq.stages[].label` — parallel composes
per-candidate (`agg1, agg2`), sequential chains checkpoints (`c1, c2`). The
stage lists *are* the difference between the modes.

**If something looks wrong / cleanup:** one output directory per experiment
keeps runs comparable; `--k` and mode live in the namespace name, so mixing
them in one directory is what "wrong" looks like here. Delete the directory.

**Next:** Stage 4 is where model calls — and money — begin.

## Stage 4 — Prepare one model-backed task

**End state:** one real LAB task executed, judged, and reported, with its cost
known. **Cost:** ~45 min setup + task runtime; **paid API calls**.

Prerequisites (beyond the offline stages): Node.js 20+, the Google Cloud CLI,
models configured — see the [setup guide](SETUP.md).

```bash
cp .env.example .env   # then edit: Vertex AI project and model IDs
gcloud auth application-default login

harness2-setup lab
harness2-check --bench lab          # readiness gate; fix what it names
harness2 --bench lab --k 1 --mode parallel \
  --only-domains antitrust-competition --max-tasks 1 --dry-run   # task preview, $0
harness2 --bench lab --k 1 --mode parallel \
  --only-domains antitrust-competition --max-tasks 1             # the paid run
harness2-judge --bench lab --k 1 --mode parallel
harness2-report --bench lab --k 1 --mode parallel
```

Expected evidence: the report line for `parallel k=1` (LAB reports partial
credit and all-criteria pass), `runs/lab/…` with deliverables, adopted
harnesses, and `judge.log` if judging failed (a Vertex `429` there means
quota — rerun the same judge command later).

**A meaningful modification:** change `--only-domains` / `--max-tasks` and
re-run — completed tasks are skipped, so widening the selection is
incremental spend, not a repeat. Changing models or task selection wants a
new `HARNESS2_RUNS_DIR`.

**If something looks wrong / cleanup:** interrupting is safe — repeat the
same command to resume. Before *judging*, review
[web access](EXPERIMENTS.md#web-access) so the executor could not reach
benchmark answers. Everything lives under `runs/`.

**Next:** Stage 5 points the same machinery at your own recurring work.

## Stage 5 — Point it at your own work

**End state:** a small pilot on a recurring task class of yours. **Cost:**
open-ended; offline if your executor is local, paid otherwise.

Custom tasks currently require a small **Python adapter** — there is no
built-in folder-of-tasks runner yet (that is
[#5](https://github.com/google-research/harness2/issues/5)). The contract is
[`BenchmarkAdapter`](../harness2/core/spec.py) (tasks, task_prompt,
task_files, execute, delivered, trajectory, deliverable_text, base_harness),
and the shortest honest template is
[`harness2/demo.py`](../harness2/demo.py) plus the ledger example's
`LedgerAdapter` — a file-in/answer-out job is ~40 lines. Your pilot wants
3–5 tasks of the *same class* (harnessing-level recursion needs recurrence to
pay off) and an executor you can call headlessly (OpenCode or Codex; see
[SETUP](SETUP.md#codex-executor)).

**A meaningful modification:** pick one recurring job you actually do, wrap
its inputs as files, and run Stage 3's comparison at k=1 before spending
wider.

**If something looks wrong / cleanup:** keep pilots in a throwaway
`HARNESS2_RUNS_DIR`; a pilot that cannot beat its own base harness on your
tasks is the framework telling you a script might be enough — that is a
useful answer, not a failure.

**Next:** the [benchmark guides](BENCHMARKS.md) for the shipped benchmarks and
the [experiment guide](EXPERIMENTS.md) for selection, concurrency, and
reproducibility.
