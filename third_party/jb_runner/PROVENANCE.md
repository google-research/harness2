# job-bench-eval runner assets

The files in this directory derive from the job-bench-eval integration and retain its
[upstream license](LICENSE). See the [pinned revision and patches](../../../dependencies/PINNED.md).

What is still vendored here:

| Path | Origin |
|---|---|
| `run_benchmark_codex_cli_adapt.sh` | Upstream `eval/run_benchmark_codex_cli.sh`, kept close to the original |
| `codex_base_harness/AGENTS.md` | Upstream Codex baseline harness |
| `harness_baseline_oc/` | Upstream OpenCode baseline harness tree |

The integration adds editable harness installation and configurable models,
paths, and endpoints. Local model-provider names, environment variables, and
log paths use the `harness2` namespace. The adapter invokes the Codex launcher
directly from the package.

The OpenCode launcher that used to sit beside it,
`run_benchmark_opencode_harness_adapt.sh`, was a fork that shared almost nothing
with upstream. It has been replaced by the first-party Python module
[`harness2/benchmarks/jb_opencode_runner.py`](../../benchmarks/jb_opencode_runner.py),
which the adapter launches as a subprocess in its place. That module is release
code, not a vendored asset, so it is not listed in `ASSETS.json`.

[ASSETS.json](../../../dependencies/ASSETS.json) records the shipped file checksums.
Verify them with `python scripts/check_provenance.py`.
