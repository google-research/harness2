# Getting started: the ledger example

A credential-free walk through one real Harness² step. Everything the method
does is real — Propose–Probe–Compose, blind selection, the improvement
library, resume — but the *editor* and the *selector* are scripted, so it runs
offline in seconds and you can inspect exactly what was saved. It makes no
claim about model quality.

## What the script does

1. Copies your CSVs (one task per file, header row `amount`, one signed amount
   per line) into `<output>/sources/<task>/ledger.csv`.
2. Runs one Harness² step (`k=1`) over every task through the shipped demo
   adapter: the base harness's `scripts/summarize.py` sums only *positive*
   amounts — a plausible-looking bug — the scripted editor proposes dropping
   that filter, and the scripted selector picks the candidate whose total
   equals the **signed** sum of the source ledger. Selection is exact and
   independent of the editor.
3. Inspects the answers the run actually saved against sums recomputed from
   `<output>/sources/`, and fails (exit 1) on any incorrect or missing answer.

## Prerequisites and run

A source checkout with the package installed (see the
[setup guide](../../docs/SETUP.md)):

```bash
uv pip install -e .
python examples/getting_started/ledger_run.py \
    --inputs examples/getting_started/inputs --output runs/ledger-example
```

Expected output ends with:

```
  ledger-1   agg PASS expected Total: 85.15  saved 'Total: 85.15'
  ...
all saved answers match the signed source totals
```

## Exercises

- **See what was saved** — `<output>/runs/demo-oc-agg1/<task>/summary.json`
  holds the stage record (which candidate fell back, what was adopted),
  `selections` names the chosen rollout, and the improvement library is under
  `<output>/library/`. Compare a base rollout's answer (positives only) with
  the selected one.
- **Change an input** — add a line to `inputs/ledger-1.csv` and re-run the same
  command. The task's fingerprint covers its source material, so only that
  task re-runs; nothing stale is silently reused.
- **Inspect without running** — `--inspect-only` re-checks saved answers.
  Tamper an `answer.txt` under `runs/.../harnesses` or an execution dir and
  watch inspection fail with exit 1.
- **Start over** — delete the output directory (or pass a new one). The script
  never writes outside `<output>`.

## Where to go next

The [benchmark guides](../../docs/BENCHMARKS.md) for model-backed runs, and the
progressive getting-started guide (in progress, #57) for a path from this
example to a bounded experiment.
