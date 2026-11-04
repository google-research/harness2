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

## Qwen Code executor (experimental)

Qwen Code is available for **JobBench only** with `--substrate qw`. The
implementation has local protocol, lifecycle, recursion and published-CLI
coverage; a real Linux JobBench run with live models remains unvalidated.
The improver still uses OpenCode. LAB, WorkBuddy and subagents are not enabled.

Use Node.js **22 or newer**. Setup installs the pinned
`@qwen-code/qwen-code@0.25.3` package under `dependencies/qwen/`; it does not use
a globally installed `qwen`. Configure the executor and improver independently:

```dotenv
HARNESS2_QWEN_MODEL=<model-id-accepted-by-your-endpoint>
HARNESS2_QWEN_BASE_URL=https://your-openai-endpoint.example/v1
HARNESS2_QWEN_API_KEY=<executor-key>
HARNESS2_IMPROVER_MODEL=<opencode-provider/model>
HARNESS2_JUDGE_MODEL=<judge-model>
HARNESS2_JUDGE_BASE_URL=https://your-judge-endpoint.example/v1
HARNESS2_JUDGE_API_KEY=<judge-key>
```

The executor currently supports OpenAI Chat Completions with an explicit key.
It does not inherit OAuth or personal Qwen settings. Configure the improver's
provider credentials separately; Vertex improvers need ADC as described above.
An OpenCode executor model is not required when an explicit improver model is set.
Keep credentials in the shell or untracked `.env`, not in harness files.

```bash
harness2-setup jb --substrate qw
harness2-check --bench jb --substrate qw
harness2 --bench jb --substrate qw --k 1 --mode parallel --dry-run
```

Choose exactly one installed profession from the dry-run output, then run:

```bash
harness2 --bench jb --substrate qw --k 1 --mode parallel \
  --only-domains <profession> --max-tasks 1
harness2-judge --bench jb --substrate qw --k 1 --mode parallel --bases
harness2-judge --bench jb --substrate qw --k 1 --mode parallel
harness2-report --bench jb --substrate qw --k 1 --mode parallel
```

For acceptance, expand this smoke run to two consecutive tasks in both modes,
then add `k=2` runs for actual candidate selection and multi-step recursion. See
the [recommended Linux acceptance matrix](design/qwen-code.md#recommended-linux-acceptance).

Each attempt uses a fresh Git workspace and `QWEN_HOME`. A generated empty
`.qwen/.env` stops ancestor dotenv lookup. Personal `~/.env` or `~/.qwen/.env`
settings cause preflight to fail with their source path; run under a clean
benchmark account instead of importing them into an experiment. Supplemental instructions,
`QWEN.md`, project skills, Python helpers and synchronous PreToolUse/PostToolUse
command hooks form the editable harness. Other skill frontmatter, subagents,
arbitrary settings and OpenCode plugins are rejected. See the
[design](design/qwen-code.md) ([中文](design/qwen-code.zh-CN.md)) for the mapping.

The initial runtime profile honors system policy but supports only empty or
UI/telemetry-only system settings in strict JSON. Other system settings fail
preflight with their source path; they are not overwritten. This intentionally
conservative check does not establish endpoint/model availability.

A run is reusable only after normal exit, validated terminal success, complete
tool results and copied deliverables. Failed attempts stay unmarked; rerunning
archives the latest three invalidated attempts under `model_traj/_failed_attempts/`.
Changing the model, endpoint, package, Node, policy or runtime profile requires a
new experiment root. Missing executor USD cost remains unknown; report spend
does not include a complete Qwen executor bill.

Local integration verification, without paid model calls:

```bash
python -m unittest discover -s tests -v
HARNESS2_TEST_QWEN_CLI=1 python -m unittest discover -s tests -p test_qwen.py -v
```

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

Codex runs with its sandbox disabled. Review the
[executor network restrictions](EXPERIMENTS.md#web-access) before evaluation.

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
