# Native CLI subscriptions

This opt-in transport uses the installed Claude Code and Codex CLIs and their
existing subscription logins. It does not use OpenCode, an API proxy, Vertex,
or API credentials. The default API transport remains available.

Currently supported: LAB with a Codex task executor. The improver, selector,
reflection model and rubric judge can use Claude Code or Codex independently.
JobBench and WorkBuddy still use their original API integrations.

## Setup

Install Python 3.12, uv, Git, Pandoc, Poppler, Claude Code and Codex. Log in to
Claude Code with a Claude subscription and Codex with a ChatGPT subscription.
The installation check verifies the login type without reading credential values.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[render]'
cp .env.subscription.example .env
# Set HARNESS2_CODEX_MODEL to a model available to your Codex account.
.venv/bin/harness2-setup lab --substrate cx
.venv/bin/harness2-check --bench lab --substrate cx
```

The setup command installs LAB's pinned dependencies and bundled document
helpers. It does not install or launch OpenCode, Bun, or a second Codex CLI.
The native runner uses Python process timeouts, so GNU `timeout` is unnecessary.
Binary documents are parsed using LAB's installed Python environment on the host.
Codex login shells are disabled for these calls to preserve that environment's PATH.
The native Codex transport requires named permission profiles. This integration
has been exercised on macOS with Codex 0.163.0 and Claude Code 2.1.309; Linux has
not been validated.

## Choose the native CLI for each role

| Role | Setting | Values |
|---|---|---|
| Task executor | `HARNESS2_CODEX_MODEL` | A native Codex model ID |
| Improver, selector, reflection | `HARNESS2_IMPROVER_CHANNEL` | `claude_cli` or `codex_cli` |
| Their model | `HARNESS2_IMPROVER_MODEL` | A model accepted by that CLI |
| Rubric judge | `HARNESS2_JUDGE_CHANNEL` | `claude_cli` or `codex_cli` |
| Judge model | `HARNESS2_JUDGE_MODEL` | A model accepted by that CLI |

For a Codex improver or judge, change both its channel and model together.
Use a new `HARNESS2_RUNS_DIR` when changing a model, channel, CLI version or runtime
implementation. The experiment identity includes transport, channels and CLI versions.
Model aliases can change upstream; use explicit model IDs for comparisons.

```bash
.venv/bin/harness2 --bench lab --substrate cx --k 1 --mode parallel \
  --only-domains antitrust-competition --max-tasks 1
.venv/bin/harness2-judge --bench lab --substrate cx --k 1 --mode parallel
.venv/bin/harness2-report --bench lab --substrate cx --k 1 --mode parallel
# Optional reference comparison:
.venv/bin/harness2-judge --bench lab --substrate cx --k 1 --mode parallel --bases
```

The recurrence, candidate validation, blind selection, reflection and domain
library still use the existing core. At k=1, selection is trivial; k>=2 exercises
the selector. Judge prompts, per-criterion document extraction and all-pass
aggregation come from LAB. Its filename-matching fallback also uses the selected
CLI, rather than silently constructing an Anthropic API client.

## Session lifetime and stale harnesses

Each rollout copies the candidate into a new temporary workspace and starts a new
`codex exec --ephemeral` process. Each improvement call starts a new temporary
context and a new CLI conversation. Claude uses `--no-session-persistence`; no
call uses `resume`, `continue` or an existing app-server thread. Existing desktop
apps and unrelated conversations do not need to restart for these trials.

Each call also receives private temporary and cache directories. Codex tools use
a named permission profile that denies the host filesystem by default, permits
the workspace and necessary system/dependency reads, and denies shared `/tmp`
and sibling temporary workspaces. This matters even with fresh conversations:
an earlier rollout's scratch files can otherwise leak into the next rollout.
LAB's original task/rubric directory is outside the permitted read roots. The
profile is passed to the child process; it does not change global CLI settings.

This does **not** deploy changes into an already-running conversation. Such a
conversation can retain earlier instructions and tool results. Harness² has no
live-session migration or global restart manager. Adopting a candidate in a
separate interactive app is a separate operation.

The CLIs retain their normal credential stores. Codex ignores user config for
these calls and explicitly selects the native OpenAI provider; it still retains
native policy and instruction loading. Claude retains its user settings and
hooks. Fresh conversations are not a claim that every machine-wide setting,
global instruction, skill or memory is isolated. Record those settings when
comparing results. Do not edit global instructions while an experiment is running.

## Evidence and failures

Executor `config.json` records the source harness fingerprint and SHA-256 hashes
of installed files. `transport/` retains the CLI output, errors, conversation ID,
requested model and usage metadata. Improver transport records are under the
namespace's audit directory; judge calls are separate from improvement evidence.

Nonzero CLI exits, incomplete Codex streams and malformed judge responses raise
errors. They do not become completed rollouts or zero-scored evaluations.
Completed rollout reuse still requires the existing harness and output fingerprints.

Subscription limits still apply. The method's USD fields are not subscription
billing measurements. Claude's list-price estimates remain in the raw transport
records and are not added to the API-spend ledger.

Codex task tools inherit the workspace permission profile with the additional
read restrictions above, and have no approval fallback. Codex judges can read
their own workspace and write only their runtime temporary/cache directories.
Claude improvers have file and shell tools; Claude judges have no tools. The
Claude branch sets private temporary directories but does not provide the same
OS-enforced host-read restrictions. Hosted web search, global instructions and
CLI startup/hooks are not covered by the Codex tool-filesystem boundary. These
are operational isolation measures, not a guarantee against all benchmark
contamination. Results are affected by CLI system instructions and model
versions, and are not directly comparable to the paper's original configuration.

## Local regression checks

```bash
python -m unittest discover -s tests
# Additional macOS checks: runs local commands in Codex's sandbox, no model calls.
HARNESS2_TEST_NATIVE_SANDBOX=1 python -m unittest discover -s tests \
  -p test_subscription_sandbox.py -v
```

The native sandbox checks exercise the permission flags emitted by the transport,
including a control that removes the boundary and exposes synthetic sibling,
shared-temporary and repository files. They do not access real credentials.
