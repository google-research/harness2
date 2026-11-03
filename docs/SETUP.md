# Setup guide

Start with the [quick start](../README.md#install). This guide covers benchmark
dependencies, model credentials, and the optional Codex executor. For individual
benchmarks, see the [benchmark guides](BENCHMARKS.md).

## Requirements

Use Linux and Python 3.12 for benchmark runs; the Python package also supports
3.13. On Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y git curl unzip ca-certificates pandoc poppler-utils
```

Pandoc and Poppler are needed by LAB. Also install:

- [Node.js](https://nodejs.org/en/download) 20 or newer.
- [uv](https://docs.astral.sh/uv/getting-started/installation/).
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
- [Docker Engine](https://docs.docker.com/engine/install/) for WorkBuddy-Bench,
  with permission to run `docker info`.

Benchmark setup installs its own pinned Bun runtime under `dependencies/`.
Keep the main Harness² environment active when running the commands below.

## Models and credentials

Copy `.env.example` to `.env` if you have not already done so. Set your Vertex AI
project and model IDs, then authenticate:

```bash
gcloud auth application-default login
```

Shell environment variables take precedence over `.env`.
`GOOGLE_CLOUD_PROJECT` identifies your project; `GOOGLE_CLOUD_LOCATION` defaults
to `global`. Your account must have access to each configured model.

The agents and judges Harness² starts do not inherit the judge, bridge,
provider, cloud and host tokens listed in `SCRUB_ENV` (`harness2/config.py`).
Each keeps only the key its own model call needs, so a harness tool that needs
another listed token, such as `GITHUB_TOKEN`, does not receive it. Variables not
on that list are passed through unchanged. Local helpers, such as the render
bridge, still receive the full environment.

| Setting | Meaning |
|---|---|
| `HARNESS2_SOLVER_MODEL` | Executor model in OpenCode's `provider/model` format |
| `HARNESS2_IMPROVER_MODEL` | Improver model in the same format; defaults to the executor |
| `HARNESS2_JUDGE_MODEL` | Bare judge model ID; the adapter supplies the Vertex prefix |
| `HARNESS2_RUNS_DIR` | Output root; defaults to `runs/` |

The provided configuration uses Vertex Gemini executors and judges, and supports
either Vertex Claude or Gemini as the improver. LAB also supports the upstream
judge's direct providers when their credentials are configured. For WorkBuddy,
setup generates the Gemini solver and judge routes from these settings; rerun
setup after changing either model.

### Authentication alternatives

On a machine with a configured attached service account, Application Default
Credentials can use that account. Other options include:

- `gcloud auth application-default login --no-browser`, which prints a command
  to run on another machine that has a browser.
- `GOOGLE_APPLICATION_CREDENTIALS` pointing at a service account key file.
- `gcloud auth application-default login --impersonate-service-account=<account>`.

Keep `gcloud` on `PATH` for these Vertex configurations, including the key-file
route: the installation check runs
`gcloud auth application-default print-access-token`.

## Benchmark installation

Install only the benchmark you intend to run:

```bash
harness2-setup lab
harness2-check --bench lab
```

Use `jb` for JobBench or `wb` for WorkBuddy-Bench. See the
[benchmark guides](BENCHMARKS.md) for subset selection and licensing.

The runner overlays and compatibility patches are included in
[`third_party/`](../third_party/README.md). Setup clones supported upstream
revisions under `dependencies/`, applies the patches, creates the benchmark
environments, and downloads task data.

Each benchmark environment lives inside its upstream checkout:

- `dependencies/harvey-labs/.venv`
- `dependencies/job-bench-eval/.venv`
- `dependencies/workbuddy-bench/.venv`

Harness² selects these interpreters automatically; do not activate them for the
main commands.

### Installation checks

`harness2-setup <bench> --dry-run` previews setup without making changes.
`harness2-check` checks installed dependencies, credentials, and configuration
without calling a model. Refinement runs perform the same checks, including
judge configuration, before starting.

These checks do not test model availability. Access to an executor, improver, or
judge model is tested when that model is first called. Refinement and judging
return a nonzero exit status when incomplete.

## Codex executor

The Codex executor connects to a Responses API endpoint. For Gemini on Vertex
AI, the included `harness2-bridge` provides this interface through a local
LiteLLM server, using the same Application Default Credentials and
`GOOGLE_CLOUD_PROJECT`.

Install the optional dependencies and start the bridge in a separate session:

```bash
uv pip install -e '.[render,bridge]'
harness2-bridge                 # for WorkBuddy: harness2-bridge --host 0.0.0.0
```

The bridge prints the `HARNESS2_CODEX_*` settings to add to `.env`. Its port and
generated access key are stored under `dependencies/codex/` and reused on
restart. Pass `--substrate cx` to setup, checks, refinement, judging, and
reporting:

```bash
harness2-setup lab --substrate cx
harness2-check --bench lab --substrate cx
harness2 --bench lab --substrate cx --k 1 --mode parallel \
  --only-domains antitrust-competition --max-tasks 1
```

WorkBuddy executes Codex inside Docker containers, so the bridge must listen on
`--host 0.0.0.0`; it also prints `HARNESS2_CODEX_CONTAINER_URL`. This binds the
bridge to all network interfaces; restrict access to the intended clients.

To use another Responses API endpoint, set `HARNESS2_CODEX_BASE_URL`,
`HARNESS2_CODEX_API_KEY`, and `HARNESS2_CODEX_MODEL` directly. Setup records the
endpoint and model in `dependencies/codex/home/config.toml`; rerun setup after
changing either. `harness2-check` reports stale configuration until then.

In API mode, Codex runs with its sandbox disabled. Review the
[executor network restrictions](EXPERIMENTS.md#web-access) before evaluation.

### Saved ChatGPT login for native runs

The default `HARNESS2_CODEX_AUTH_MODE=api` uses the Responses endpoint described
above. Set `HARNESS2_CODEX_AUTH_MODE=chatgpt` to use an existing Codex CLI login
for native JobBench and LAB execution. The CLI manages authentication and token
refresh; Harness² does not read or copy credentials. WorkBuddy's container
executor does not support this mode.

Install a current Codex CLI and run `codex login` with your ChatGPT account.
Then select a model available to that account. For example:

```bash
export HARNESS2_CODEX_AUTH_MODE=chatgpt
export HARNESS2_CODEX_MODEL=gpt-5.3-codex
export HARNESS2_IMPROVER_CHANNEL=codex_cli
codex login status
```

The improvement channel is independent of the executor. `codex_cli` runs the
proposal, composition, selection, and reflection sessions through `codex exec`.
Its model defaults to `HARNESS2_CODEX_MODEL`; set `HARNESS2_IMPROVER_MODEL` to use
a different bare model ID. The existing `opencode` default is unchanged, and
`claude_cli` is also selectable through `HARNESS2_IMPROVER_CHANNEL`.

Account mode uses `codex` on `PATH`. Set `HARNESS2_CODEX_BIN` for another installed
CLI and `HARNESS2_CODEX_HOME` for an existing authentication home; otherwise the
CLI uses the inherited `CODEX_HOME` or its normal default. Setup checks
`codex login status` and does not install the API-mode pinned Codex distribution
or generate a provider configuration. Native account sessions ignore user
configuration, use the OpenAI provider with saved ChatGPT authentication, and
run in the workspace-write sandbox. Generated harness files and editable hooks
retain their existing execution semantics.

To exercise the real recursion workflow without installing a benchmark or a
judge, run the opt-in Codex ledger demo:

```bash
harness2-demo --substrate cx --max-tasks 1 --k 1 --mode parallel \
  --output runs/codex-login-demo
```

This starts real Codex sessions and consumes the account's model quota. JSONL
trajectories include CLI-reported token usage. The CLI does not report monetary
subscription spend: `codex_cli` audit records mark `cost_reported: false`; a zero
USD field is not evidence of free usage. Use a new output directory when changing
models or demo options. The default demo remains deterministic and offline.

Native benchmarks use the same `--substrate cx` commands as API mode. When both
the executor and improvement channel use Codex, setup and readiness checks omit
the unused OpenCode toolchain. Benchmark datasets, document readers, and judges
remain required. In particular, `harness2-check`, refinement preflight, and
`harness2-judge` keep the benchmark's original judge configuration and scoring
rules. A ChatGPT-based executor and improver do not make benchmark judging
API-free; configure its judge separately as documented above.

## Network access during setup

Setup downloads dependencies from the following hosts:

| Host | Used for |
|---|---|
| `github.com` | Pinned upstream checkouts |
| `registry.npmjs.org` | Pinned Bun runtime and Codex CLI, plus dependencies resolved by `bun install` |
| `huggingface.co` and its CDN | JobBench data and WorkBuddy subset archives |
| A Docker registry | WorkBuddy container image |
| `astral.sh` | CPython 3.12 when the host has no system Python 3.12 |

Use `harness2-setup <bench> --dry-run` to review setup commands when configuring
network access. Experiments also access the model endpoints configured in
`.env`. Setup downloads and executor web access have different requirements;
see [web access during evaluation](EXPERIMENTS.md#web-access).

## Wheel installations

A wheel ships the `harness2` package only. The task partitioner,
`python scripts/split_tasks.py`, requires this source tree. For a wheel
installation, work from a dedicated directory: `.env`, `dependencies/`, and
`runs/` are resolved there.
