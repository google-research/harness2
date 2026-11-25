# CLAUDE.md

Guidance for agents working in this repository.

## What this repo is

A fork of upstream Harness² (https://github.com/google-research/harness2). The
fork adds a **pi (pi.dev) substrate** on the `pi-substrate-v2` branch: `PiSubstrate`
in `harness2/substrate/pi.py`, `PI_SUBSTRATE_MODEL` in `harness2/config.py`, and the
two tests below. Everything else tracks upstream.

Read first: `README.md`, then `docs/SETUP.md` (including its pi substrate section),
`docs/BENCHMARKS.md`, and `docs/EXPERIMENTS.md`.

## Remotes

| Remote | URL | Role |
|---|---|---|
| `origin` | `https://github.com/google-research/harness2.git` | upstream project (fetch only, never push) |
| `fork` | `git@github.com:ouryuk/harness2.git` | our fork; branch around `pi-substrate-v2` |
| `private` | `ssh://git@github.com/ouryuk/harness2-private.git` | our private copy of the fork |

Do not push to `origin`. Upstream-sync changes (rebasing or merging a newer
upstream `main`, or carrying the pi work forward) happen on the fork branch,
`pi-substrate-v2`, and are published to `fork`/`private`. Never rewrite upstream
history: no force-push that discards upstream commits, no amended or reordered
commits reachable from `origin/main`.

## Substrate tests

The pi substrate tests are standalone scripts, not a pytest suite; the repo has
no CI workflow and no test-runner configuration in `pyproject.toml`:

```bash
. .venv/bin/activate
python tests/test_pi_substrate_unit.py              # deterministic, no model calls
PI_SUBSTRATE_MODEL=<provider/model> python tests/test_pi_substrate.py   # real pi agent
```

The e2e check runs a real `pi` process and needs a pi installation plus
credentials for that model route. The default route is `zai/glm-5.3`
(`harness2/config.py`).

## Conventions

- Python 3.12+ (3.13 supported), no third-party runtime dependencies in the core
  package; optional extras are `render` and `bridge`.
- Lint: `ruff check .` with the settings in `pyproject.toml`.
- Keep docs accurate to the code: `--substrate` accepts `oc` or `cx` only; the pi
  substrate is used programmatically (see `docs/EXPERIMENTS.md`).
- `dependencies/` and `third_party/` are generated/vendored; do not edit them by hand.
