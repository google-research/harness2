# DeepSeek Harness executor

`--substrate ds` executes **JobBench and LAB** with the official
[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)
runtime. The existing OpenCode improver proposes and composes candidate trees;
DeepSeek executes them. Selection and reflection use task inputs, trajectories,
and artifacts, not benchmark judgments. The existing per-domain experience
library carries adopted harnesses and lessons to subsequent tasks.

This changes the harness, **not model weights**. Executor provider/model settings
are outside the editable surface. A native `llm/stream` guard checks the fixed
provider/model for ordinary runtime calls, including auxiliary calls, and limits
the number of model calls.

## Supported runtime and integration choice

The optional extra pins **`deepseek-harness-sdk==0.2.1rc2`**, which installs the
matching `deepseek-harness-runtime-bin==0.2.1rc2` wheel. The reviewed upstream
revision is
[`687f282e8625c6b8d11ff58211a90524be2e4317`](https://github.com/deepseek-ai/deepseek-harness/tree/687f282e8625c6b8d11ff58211a90524be2e4317),
tag `dsh-v0.2.1-rc.2`. This is a developer-preview interface, not a promise of
compatibility with subsequent releases.

The adapter uses the public Python `DeepSeekHarness` / `Session` APIs, not private
launch arguments or a replacement agent loop. The SDK's `sdk-minimal` profile is
a standalone, predictable Cordis composition. The official headless CLI prints
final text; the SDK additionally supplies ordered session/tool notifications,
durable session restoration, and explicit lifecycle ownership. Normal SDK use
needs neither a separately installed Node.js nor pnpm.

The official provider uses the **DeepSeek Messages API**, not OpenAI Chat
Completions or Responses. The default root is
`https://api.deepseek.com/anthropic`; the runtime appends `/v1/messages`.
An exact trailing `/v1` is reused. A compatible gateway must implement this
Messages/SSE protocol. Do not point it at `/chat/completions`.

Verified contracts:
[SDK reference](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/python/sdk/README.md),
[minimal profile](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/bundle/sdk-minimal/cordis.patch.yml),
[Messages provider](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/llm/llm-deepseek/README.md),
[profile layering](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/apps/cli/reference/README.md).

## Install and run without credentials

Use Python 3.12 or 3.13 in an isolated environment. Benchmark installation still
requires Linux and the dependencies in [SETUP.md](SETUP.md). The SDK wheel and
the local demonstration also work on supported Windows x64 and macOS systems.

```powershell
$env:PYTHONUTF8 = "1"
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[deepseek]"
.\.venv\Scripts\python -m harness2.cli.deepseek_demo run --output .\runs\deepseek-demo --k 1 --mode both
.\.venv\Scripts\python -m harness2.cli.deepseek_demo evaluate --output .\runs\deepseek-demo --k 1 --mode both
```

On Linux/macOS, activate the environment and use:

```bash
python -m pip install -e '.[deepseek]'
harness2-deepseek-demo run --output runs/deepseek-demo --k 1 --mode both
harness2-deepseek-demo evaluate --output runs/deepseek-demo --k 1 --mode both
# A repeated run resumes; it must make no new provider requests.
harness2-deepseek-demo run --output runs/deepseek-demo --k 1 --mode both
```

This is **the real SDK, bundled runtime, persistent shell, and filesystem tools**
against an explicitly local, deterministic Messages server. It does not mock
`DeepSeekHarness`. The server instructs the runtime to execute each candidate's
Python ledger helper and write its stdout as a deliverable. The baseline helper
incorrectly ignores negative amounts; the scripted editor removes that filter.
The composer and reflector use the existing Harness2 workflow. Two tasks exercise
experience reuse. Both recursion modes run with one improvement step.

The fixture's provider responses and token counts are synthetic; there are **no
model API charges**. Its correction is designed, not a measured improvement of a
real model. A live solver may not improve, may fail, or may ignore a helper.
The `run` command never evaluates. `evaluate` separately records baseline and
adopted exact-answer results in `evaluation.json`. `demo.json` records workflow
outcomes, editor calls, and fixture requests. Reusing the output directory also
reuses the recorded loopback port; if it is occupied, stop that service or choose
a new output directory rather than mixing identities.

The smoke test is explicitly opt-in and incurs API charges:

```powershell
$env:DEEPSEEK_API_KEY = "<your key>"
$env:HARNESS2_DEEPSEEK_MODEL = "deepseek-v4-flash"  # choose a model your endpoint serves
python -m harness2.cli.deepseek_demo smoke --live --output .\runs\deepseek-live-smoke
```

`run --live` uses the real configured solver with the same scripted editors,
instead of the local server. Use a different output directory from the fixture.

## Run a model-backed benchmark

Install the ordinary benchmark data and improver dependencies as well as the SDK.
The DeepSeek executor does not replace the editor/selector channel or the judge.

```bash
export DEEPSEEK_API_KEY='<your key>'
export HARNESS2_DEEPSEEK_MODEL=deepseek-v4-flash
# Optional Messages-compatible root; never an OpenAI Chat/Responses URL:
# export HARNESS2_DEEPSEEK_BASE_URL=https://your-gateway.example/anthropic
export HARNESS2_IMPROVER_MODEL='provider/editor-model'
export HARNESS2_JUDGE_MODEL='your-judge-model'
export HARNESS2_RUNS_DIR=runs/lab-deepseek
# Configure the editor and judge's credentials/providers as in SETUP.md.

harness2-setup lab --substrate ds
harness2-check --bench lab --substrate ds
harness2 --bench lab --substrate ds --k 1 --mode parallel \
  --only-domains antitrust-competition --max-tasks 1

# Only after refinement, selection, and reflection:
harness2-judge --bench lab --substrate ds --k 1 --mode parallel --bases
harness2-judge --bench lab --substrate ds --k 1 --mode parallel
harness2-report --bench lab --substrate ds --k 1 --mode parallel
```

Use `jb` instead of `lab` for JobBench, and choose one of its installed
occupational domains. `--mode sequential` and larger `--k` use the same shared
recursion implementation. `SUBSTRATE=ds` also works with `scripts/run_lab.sh`
and `scripts/run_jobbench.sh`. Those convenience scripts judge immediately
after refinement; use separate CLI commands when you want to inspect the
answer-free evidence before evaluation.

`HARNESS2_SOLVER_MODEL` is not required for `ds` if `HARNESS2_IMPROVER_MODEL`
is explicit. That improver still uses OpenCode's `provider/model` syntax.
The normal preflight still checks the improver and benchmark/judge environments.
DeepSeek preflight checks credentials, exact package versions and `dsh --version`;
it does not contact a model, resolve provider availability, or validate billing.

JobBench exports only deliverables to its existing `model_output/<label>` store;
worker evidence is in `model_traj/<label>`. LAB retains its `output/` convention
and existing judge. Both give the executor a filtered copy of source material,
not rubric files or LAB's `task.json`. The benchmark's installed Python environment
is placed first on the tool PATH and advertised explicitly. LAB's editable
`scripts/read_doc.py` invokes its existing parser with that interpreter.

**WorkBuddy is not supported by this executor yet.** Its Harbor/Docker environment,
mounts, proxy, and verifier require a separate container integration. `wb + ds`
fails before setup/execution with an actionable error. Running its task text on
the host would not be a valid WorkBuddy result; use `oc` or `cx` for that benchmark.

## Configuration

Shell values override `.env`, as elsewhere in Harness2. These settings belong
to the deployment, never the candidate tree.

| Variable | Default / meaning |
|---|---|
| `HARNESS2_DEEPSEEK_MODEL` | `deepseek-v4-flash`; exact native model ID, fixed across candidates |
| `HARNESS2_DEEPSEEK_BASE_URL` | `DEEPSEEK_BASE_URL`, else `https://api.deepseek.com/anthropic` |
| `HARNESS2_DEEPSEEK_API_KEY` | Optional credential override; otherwise read the variable below |
| `HARNESS2_DEEPSEEK_API_KEY_ENV` | `DEEPSEEK_API_KEY`; name of the credential variable |
| `HARNESS2_DEEPSEEK_BIN` | Optional absolute `dsh` executable; must be the supported version |
| `HARNESS2_DEEPSEEK_PROFILE` | `sdk-minimal`; other profiles are rejected, not silently substituted |
| `HARNESS2_DEEPSEEK_PROVIDER` | `deepseek-official`; other routes need an explicit mapping |
| `HARNESS2_DEEPSEEK_REASONING_EFFORT` | `high`; accepts `off`, `low`, `high`, `max` |
| `HARNESS2_DEEPSEEK_MAX_TOKENS` | `49152`; SDK per-request root/descendant output cap, not a dollar limit |
| `HARNESS2_DEEPSEEK_MAX_MODEL_CALLS` | `120`; native stream-call limit per invocation, including retries/compaction |
| `HARNESS2_DEEPSEEK_WALL_TIMEOUT` | `3600` seconds per benchmark rollout; API callers can override `timeout` |
| `HARNESS2_DEEPSEEK_CONTEXT_WINDOW` | `1000000`; fallback capacity for unlisted models; must leave compaction headroom |
| `HARNESS2_DEEPSEEK_SHELL_TIMEOUT` | `300` seconds per shell operation |
| `HARNESS2_DEEPSEEK_INITIALIZE_TIMEOUT` | `30` seconds for the SDK handshake and individual RPCs |
| `HARNESS2_DEEPSEEK_SHUTDOWN_TIMEOUT` | `5` seconds of graceful SDK shutdown |
| `HARNESS2_DEEPSEEK_MAX_PARALLEL_TOOLS` | `1`; native tool-call concurrency |
| `HARNESS2_DEEPSEEK_TEXT_TOOLS` | `true`; enables native `read`, `write`, and `edit` alongside shell/image/directory tools |

Wall time bounds the entire worker, not just an individual SDK request.
Cancellation/timeout allows a bounded shutdown grace before killing remaining
owned descendants by PID. An SDK `max-tokens`, error, startup failure, or missing
result is **not** a completed rollout and is never cached as a valid zero.
Normal completion without an artifact remains distinct from a transport failure.
The runtime may compact context using additional calls; its compaction summaries
have their own native token limit. Model-call and wall limits are not billing
guarantees. Parallel probes multiply the number of concurrent paid sessions.

## Editable component mapping

| Harness2 component | DeepSeek mechanism | Limits |
|---|---|---|
| c1 `systemprompt.md` | Native system-prompt section | Literal text, not Jinja; no model settings |
| c2 `guardrails.md` | Same always-on section | Guidance, not a security boundary; use a native guard for enforcement |
| c3 `LongTermMEMORY.md` | Explicit candidate-local always-on experience | No implicit shared home or previous-task conversation |
| c4 `tool_descriptions/tool_guidance.md` | Always-on tool guidance | Does not rewrite native parameter schemas |
| c5 `sub_agents/*/AGENT.md` | Unsupported in this mapping | Rejected; turn procedures into skills or use another executor |
| c6 `skills/*/SKILL.md` | Advertised name, description and absolute path; agent reads body on demand | Requires matching `name:`, `description:` and an always-on trigger; no fake `skill` tool |
| c7 `scripts/*.py`, skill scripts | Files copied with the candidate; invoked by the real shell | Python syntax checked; referenced helpers must ship |
| c8 `plugins/*.mjs` | Native Cordis modules inserted into the profile | ES modules, not OpenCode TypeScript plugins or Codex hooks |

All four always-on components share a 24,000-character validation cap.
The profile is rendered as JSON (valid YAML); an adapter-owned native plugin
registers the prompt with `interpolate: false`, preserving literal braces.
Candidate files live in a private harness directory, advertised in the prompt,
so installing helpers cannot overwrite task files with the same names.
Paths such as `scripts/foo.py` in candidate instructions refer to that directory.

Example native plugin:

```javascript
export const inject = ["systemPrompt"];
export function apply(ctx) {
  ctx.effect(() => ctx.systemPrompt.section({
    name: "candidate:reopen", order: 10,
    text: "Reopen generated files before finishing.", interpolate: false,
  }));
}
```

Native tool policies can use the pinned runtime's `ctx.tools.guard` and
`tools/pre-execute`, `tools/post-execute`, and `tools/result` interfaces.
Modules may import bundled DeepSeek/Cordis packages and Node built-ins. Arbitrary
npm dependency installation, custom persistent bundles/profiles, full `sdk`
services, and OpenCode/Codex plugin translation are not implemented. Use a small
Python helper or an explicit native module rather than shipping an inert config.
Bad plugins fail at native startup with diagnostics before a model runs.
The SDK server is wired to readiness services published only after every
candidate plugin and the adapter's own prompt/budget plugin activates. This
avoids Cordis's default behavior of merely warning about a failed optional plugin.
Do not write to stdout from a plugin: it carries the SDK JSON-RPC protocol.

## Isolation, evidence, and resume

Each independent attempt gets a fresh workspace, `DSH_HOME`, and session UUID.
No ambient `~/.dsh`, personal API keys for other providers, or judge credentials
are inherited. A disposable process home/cache also avoids Windows native-module
cache path limits; it is removed after the worker and its descendants stop.
Durable SDK sessions remain in the attempt's explicit DeepSeek home.
Candidate staging directories are removed even on failure; durable run evidence
and attempt histories are intentionally retained.

The original `Install.fingerprint` remains a source-tree fingerprint. A separate
execution fingerprint incorporates the staged tree, deployment settings, SDK
and runtime versions, native adapter plugin, and optional executable hash.
Rollout identity additionally includes inputs, task prompt, timeout, and
benchmark provenance. Completed output and event fingerprints are verified before
reuse; failed or altered runs get new attempts rather than inheriting a partial
conversation. Experiment configuration refuses incompatible model/settings
resumes. Credentials are deliberately not part of the fingerprint.

Per-run files include:

| File | Meaning |
|---|---|
| `deepseek_run.json` | Identity, state, session UUID, workspace/home paths, artifact fingerprints |
| `deepseek_session.jsonl` | SDK notifications, including events, tool calls/results, errors and status |
| `metrics.json` | Outcome, wall time, available native usage counters; dollar cost is `null` when unknown |
| `final_response.txt` | Final root assistant response, separate from requested deliverables |
| `request.json` | Nonsecret execution request; no API key |
| `worker.log` | Worker/startup diagnostics with the configured key redacted |
| `output/` | Copies of agent-authored deliverables only |
| `attempts/<id>/` | Candidate files, workspace, isolated home, and per-invocation evidence |

Rendering preserves the existing `[ACTION]`, `[OBSERVATION]`, `[ERROR]` grammar
and adds available `[USAGE]` records. Malformed/truncated records are visible.
Full notifications and native home logs remain available beyond the rendered
view. An incomplete rollout's evidence directory survives into refinement
contexts instead of being replaced by a nonexistent path.

Ordinary benchmark resume is **idempotent reuse**, not conversation continuation.
For an intentional same-task follow-up, the Python API offers:

```python
substrate.continue_session(
    install, run_dir=run_dir,
    prompt="Reopen the delivered files and correct any malformed output.",
    timeout=120,
)
```

This calls the SDK's `resume_session`, requires the same harness/runtime identity
and existing home/workspace, and appends evidence. Missing sessions fail; they
are never replaced silently. It does not restore shell/browser processes.
A later independent execution does not reuse a continued result as its original
baseline. Experience for *other* tasks belongs in the existing library and
explicit candidate files, not these sessions.

## Costs, permissions, and troubleshooting

The minimal profile is **danger-full-access**, not an OS sandbox. Native plugins
and helpers execute trusted code with the user's privileges and ordinary network
access. Isolated directories prevent accidental candidate-state sharing; they
do not prevent a tool from reading other paths or accessing the network.
Use disposable machines/containers, least-privilege credentials, and external
egress controls for untrusted tasks. See [evaluation network restrictions](EXPERIMENTS.md#web-access).
Prompt guardrails are not equivalent to those controls.

The adapter disables the minimal profile's default `dsh_session_log` and
`dsh_plugin_packages` uploads. Model-visible task data, prompts, tool output, and
any requested images still reach the configured inference endpoint. Raw native
home logs can contain task data and plugin-produced diagnostics; restrict access
and review them before sharing. No credentials are written into configuration,
command arguments, candidate trees, or experience-library entries by the adapter.

| Symptom | Action |
|---|---|
| Missing SDK/runtime or unsupported version | Install `.[deepseek]` in the interpreter running Harness2; do not mix SDK/runtime versions |
| 401/403 or unknown model | Check the selected key variable, Messages endpoint and account model access; preflight is intentionally nonbillable |
| 404 / invalid provider response | Use a Messages-compatible API root, not an OpenAI API route |
| Plugin startup failure | Inspect `worker.log` and the isolated home's startup logs; remove unsupported imports/configuration and rerun |
| Timeout or `HARNESS2_BUDGET` | Inspect partial evidence; increase the appropriate deployment budget in a new experiment directory if warranted |
| Missing document libraries | Run benchmark setup; use its advertised Python interpreter, and install LAB's Pandoc/Poppler |
| Missing shell tools or native library | Use a supported runtime wheel/platform; text tools work independently of optional authoring runtimes |
| Windows long-path cleanup | Keep experiment paths short or use long-path-aware tools; native session directories include a readable cwd slug |
| Resume refuses changed settings/data | Use a new `HARNESS2_RUNS_DIR`; do not overwrite the saved identity to force reuse |
| WorkBuddy selection rejected | Use `oc`/`cx`; a DeepSeek Harbor agent is still required |

## Verification and remaining live gaps

```bash
python -m unittest discover -s tests -v
python -m ruff check harness2 tests
```

Install the optional extra to run native contract tests; without it those tests
are explicitly skipped. They cover actual SDK startup, native plugin/component
activation, candidate-sensitive shell execution, real tool observations, input
filtering in JobBench/LAB, isolated sessions, cached resume, deliberate SDK
continuation, call budgets, cancellation/descendant cleanup, reflection, library
reuse, and separate evaluation.

The real-endpoint test is skipped unless explicitly enabled:

```powershell
$env:HARNESS2_DEEPSEEK_LIVE = "1"
python -m unittest discover -s tests -p test_deepseek.py -v
```

### Recorded local results

Verified on 2026-10-20 with Python 3.12.6 on Windows x64 and the pinned
`0.2.1rc2` SDK/runtime. The reproduction above used `--k 1 --mode both`:

| Observation | Measured outcome |
|---|---|
| Ledger 1 | Baseline `105.25`; both adopted harnesses `85.15` |
| Ledger 2 | Baseline `35.50`; both adopted harnesses `20.25` |
| Separate exact-answer evaluation | Baseline 0/2; parallel 2/2; sequential 2/2 |
| Runtime executions | 14 completed rollouts; 42 local Messages requests |
| Scripted improvement calls | 16, including composition and reflection |
| Experience reuse | Task 2 received one prior library entry in each mode |
| Repeated command | Both tasks skipped; zero executor, editor, or provider calls |
| Regression suite | 20 tests passed on Windows and Linux; one credential-gated live test skipped on each |
| Other checks | Ruff passed; wheel build and native-plugin/entry-point packaging checks passed |

The arithmetic correction is intentionally built into this deterministic fixture;
these are integration measurements, not JobBench/LAB scores or a claim of real-model
improvement. At `k=1` the two modes are the same procedure. No production model
credentials or full benchmark datasets were available for live evaluation.
Linux validation used Ubuntu under WSL2, Python 3.12.3, x86_64, and glibc 2.39;
the full suite included the real Linux runtime, native shell/tools, continuation,
cancellation, plugin activation failures, and both benchmark transports.

Local fixture success does not establish account authentication, production
model quality, cloud reliability, full downloaded-benchmark scores, or WorkBuddy
container compatibility. Record those separately when running with the required
credentials and infrastructure.
