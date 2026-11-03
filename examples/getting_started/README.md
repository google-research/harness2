# Local ledger exercises

Start with the [progressive guide](../../docs/GETTING_STARTED.md). These
examples extend `harness2-demo`; they do not require model credentials.

After installing the package in a source checkout, run from the repository
root:

```bash
EXAMPLE=examples/getting_started/ledger.py
uv run --no-sync python "$EXAMPLE" run \
  --input-dir examples/getting_started/ledgers \
  --output runs/ledger-example --k 1
uv run --no-sync python "$EXAMPLE" inspect runs/ledger-example
```

The selected totals are 85.15 and 20.25. `run` executes local Python through
the existing demo adapter and scripted proposer. `inspect` reads saved source
CSVs, summaries, and answer files, checks the signed sums and execution
budgets, and returns nonzero for incomplete or incorrect results. It also
accepts output directories from `harness2-demo` and multiple roots to compare.
Keep runs in place: summaries contain absolute paths to their executions.

To modify the inputs, copy the CSVs to a separate directory, edit the amounts,
and pass that directory with a fresh `--output` path. Inputs are processed in
sorted filename order. The example deliberately refuses to reuse output
directories. Remove only your generated run directory when finished.

This is a workflow demonstration with a known fix and exact-sum selection,
not a model evaluation. A different kind of task needs its own adapter and
executor; see the guide's [one-day pilot](../../docs/GETTING_STARTED.md#1-day-build-a-small-workflow-pilot).
