# Bundled third-party code

This package contains the derived benchmark runners, compatibility patches, and
upstream license notices distributed with Harness². It is included in wheels
so setup works outside a source checkout.

| Directory | Source |
|---|---|
| `jb_runner/` | Job-Bench/job-bench-eval |
| `lab_runner/` | harveyai/harvey-labs |
| `wb_runner/` | Tencent/workbuddy-bench |
| `patches/` | Compatibility changes applied to the pinned upstream repositories |
| `licenses/` | License copies covering those patches |

Each runner retains its `LICENSE` and `PROVENANCE.md`. Research logic belongs in
`harness2/core/`; benchmark interfaces belong in `harness2/benchmarks/`.
Setup downloads upstream checkouts and data into the invocation's `dependencies/`
directory. Those downloads are excluded from version control.

The repository's `dependencies/ASSETS.json` records bundled asset hashes.
Run `python scripts/check_provenance.py` after changing these files.
