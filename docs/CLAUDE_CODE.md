# Claude Code

Harness² can run on [Claude Code](https://code.claude.com/docs) in two places,
and you can use either one or both:

* **Executor** (`--substrate cc`): each rollout is a headless `claude -p`
  session in a staged task workspace. The editable harness becomes a native
  Claude Code project: `CLAUDE.md`, `.claude/skills/`, `.claude/agents/`,
  hooks in `.claude/settings.json`, and `scripts/`.
* **Improver** (`HARNESS2_PROPOSER_CHANNEL=claude_cli`): the harness editor,
  composer, selector, and experience reflector run as `claude -p` sessions
  instead of OpenCode sessions.

If you use both, you don't need OpenCode, bun, or npm. Setup and checks skip
them.

| Benchmark | `--substrate cc` |
|---|---|
| JobBench | Supported |
| LAB | Supported |
| WorkBuddy-Bench | Not supported. Its executors run inside Harbor containers, and no Claude Code container agent exists yet. |

## Configure

Install the Claude Code CLI and sign in (or set `ANTHROPIC_API_KEY`). Then add
the following to `.env`:

```bash
# Executor and improver models: bare Claude model ids or aliases, not provider/model.
HARNESS2_CLAUDE_MODEL=sonnet
HARNESS2_CLAUDE_IMPROVER_MODEL=opus        # defaults to HARNESS2_CLAUDE_MODEL
HARNESS2_PROPOSER_CHANNEL=claude_cli       # default: opencode

# Optional
HARNESS2_CLAUDE_EFFORT=high                # executor `claude --effort`
HARNESS2_CLAUDE_MAX_BUDGET_USD=5           # per-rollout `claude --max-budget-usd` cap
HARNESS2_CLAUDE_MAX_OUTPUT_TOKENS=         # CLAUDE_CODE_MAX_OUTPUT_TOKENS for rollouts
HARNESS2_CLAUDE_BIN=claude                 # path to the CLI
HARNESS2_CLAUDE_USE_VERTEX=                # 1: route the improver through Vertex AI (upstream behaviour)

# Judges are unchanged; see SETUP.md.
HARNESS2_JUDGE_MODEL=...
```

The executor uses whatever credentials the `claude` CLI already has: a
subscription login, `ANTHROPIC_API_KEY`, or a provider you selected with
`CLAUDE_CODE_USE_VERTEX` or `CLAUDE_CODE_USE_BEDROCK` and its own credentials.
The improver behaves the same way unless `HARNESS2_CLAUDE_USE_VERTEX=1`. In
that case it uses the upstream Vertex wiring (`GOOGLE_CLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION`) and ignores any Anthropic API key.

## Run

Pass `--substrate cc` to setup, checks, refinement, judging, and reporting:

```bash
harness2-setup lab --substrate cc
harness2-check --bench lab --substrate cc
harness2 --bench lab --substrate cc --k 1 --mode parallel \
  --only-domains antitrust-competition --max-tasks 1
harness2-judge --bench lab --substrate cc --k 1 --mode parallel
harness2-report --bench lab --substrate cc --k 1 --mode parallel
```

Results go to their own namespaces (for example `runs/lab/results/cc/`), so
Claude Code runs never mix with OpenCode or Codex runs.

## Editable surface

The improver edits these paths. Validation, the always-on cap, and skill
triggers work the same way as on the other executors.

| Path | Component | Installed as |
|---|---|---|
| `CLAUDE.md` | always-on prompt, guardrails, memory, tool guidance (8,000-character cap; must not be empty) | `CLAUDE.md` at the workspace root |
| `.claude/skills/<name>/SKILL.md` (+ `scripts/*`) | skills, loaded on demand by the Skill tool | the same path |
| `sub_agents/<name>/AGENT.md` | subagents (frontmatter `description:`) | `.claude/agents/<name>.md`, with `name:` set to the directory name |
| `scripts/<name>.py` | deterministic helpers | the same path |
| `.claude/hooks.json` | lifecycle hooks, in Claude Code's hook schema | the `hooks` key of a generated `.claude/settings.json` |

A proposal can add hooks, but it can't change permissions, environment
variables, or the model: `.claude/settings.json` is always generated from
`.claude/hooks.json` alone. The accepted hook events are `PreToolUse`,
`PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `Notification`,
`UserPromptSubmit`, `SessionStart`, `SessionEnd`, `Stop`, `SubagentStart`,
`SubagentStop`, and `PreCompact`.

## Isolation and trajectories

Every rollout runs:

```
claude -p <task prompt> --output-format stream-json --verbose --include-hook-events \
  --dangerously-skip-permissions --setting-sources project --strict-mcp-config \
  --no-session-persistence --add-dir <workspace> --model <model> [--effort ...]
```

The working directory is the staged workspace. `--setting-sources project` and
`--strict-mcp-config` keep your own `~/.claude` settings, `CLAUDE.md`, hooks,
plugins, and MCP servers out of the rollout, so the harness being evaluated is
the only harness present. Claude Code's built-in tools, skills, and agents are
still available, the same way OpenCode's built-in tools are on that executor.

The stream-json session becomes the shared trajectory grammar that the
improver reads:

* `[ACTION]`, `[OBSERVATION]`, `[OBSERVATION ERROR]`, `[ASSISTANT]`, and
  `[THINKING]` lines.
* `[SUBAGENT]` before any line from a delegated agent.
* `[HARNESS] hook <name> exit=<code>` each time a hook fires, so the improver
  can tell whether its hooks actually ran.

`--dangerously-skip-permissions` disables Claude Code's permission prompts.
Review the [executor network restrictions](EXPERIMENTS.md#web-access) before
evaluation, as you would for Codex.
