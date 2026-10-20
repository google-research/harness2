# OpenRouter model support — design

Date: 2026-10-20
Status: approved for implementation

## Goal

Let every benchmark (JobBench, LAB, WorkBuddy-Bench) and both substrates
(OpenCode, Codex) run against models served by OpenRouter, configured entirely
through environment variables. Vertex AI remains supported and unchanged as the
existing default.

All three model roles — executor/solver, improver/proposer, and judge — may be
OpenRouter models. No OpenRouter model IDs are baked into defaults; users set
`HARNESS2_SOLVER_MODEL`, `HARNESS2_IMPROVER_MODEL`, and `HARNESS2_JUDGE_MODEL`
to OpenCode-style IDs.

## Model ID convention

Every role is configured with the same OpenCode `provider/model` syntax:

```
HARNESS2_SOLVER_MODEL=openrouter/anthropic/claude-sonnet-4
HARNESS2_IMPROVER_MODEL=openrouter/anthropic/claude-sonnet-4
HARNESS2_JUDGE_MODEL=openrouter/openai/gpt-5
```

A provider-aware resolver strips the provider segment for APIs that want the
bare `<vendor>/<model>` (OpenRouter, JobBench) while preserving the full ID for
OpenCode.

## Architecture

### Provider resolution layer (`harness2/config.py`)

```python
@dataclass(frozen=True)
class ModelRef:
    provider: str        # "google-vertex" | "openrouter" | other OpenCode provider
    opencode_id: str     # "openrouter/anthropic/claude-sonnet-4"
    api_model: str       # "anthropic/claude-sonnet-4"
```

- `resolve_model(id)` classifies by prefix using a small registry for
  `google-vertex/...` and `openrouter/...`. Unknown prefixes remain valid
  OpenCode providers; there is no hard-coded whitelist.
- `judge_target()` returns `(base_url, api_key_env, api_model, extra_headers)`:
  1. Explicit `HARNESS2_JUDGE_BASE_URL` / `HARNESS2_JUDGE_API_KEY` wins.
  2. `openrouter/...` → `https://openrouter.ai/api/v1`, `OPENROUTER_API_KEY`.
  3. `google-vertex/gemini...` → existing Vertex behavior.
  4. Otherwise the existing bare-ID behavior.
- `validate_models()` requires `GOOGLE_CLOUD_PROJECT` only for Vertex refs and
  `OPENROUTER_API_KEY` only for OpenRouter refs.
- Derived values (`SOLVER_MODEL_OC`, `SOLVER_MODEL_CX`, `IMPROVER_MODEL`,
  `JUDGE_MODEL`, `LAB_JUDGE_MODEL`, `JB_JUDGE_*`, `WB_*`) are computed from
  `ModelRef`, replacing ad-hoc `startswith()` checks.

### OpenCode substrate

No argv changes: OpenCode's registry resolves `openrouter/<vendor>/<model>`.
`run_env` inherits `os.environ`, so `OPENROUTER_API_KEY` reaches the subprocess.
No generated `opencode.json` provider block is required.

### JobBench judge

`judge_target()` supplies `JB_JUDGE_API_BASE`, `JB_JUDGE_API_KEY`, and
`api_model` to the upstream OpenAI-compatible judge. `preflight` requires a base
URL and key for non-Vertex judges and does not demand gcloud for OpenRouter.

### LAB judge

Extend `third_party/patches/harvey-labs-judge-vertex-anthropic.diff`:

- `_detect_provider` recognizes an `openrouter/` prefix.
- The client is `OpenAI(base_url="https://openrouter.ai/api/v1",
  api_key=os.environ["OPENROUTER_API_KEY"])`.
- The API model strips the `openrouter/` prefix.

`LAB_JUDGE_MODEL` passes through unchanged, mirroring today's `vertex/...`
convention. `_lab_env` inherits the key. Missing key raises a clear error rather
than falling through to the Mistral path.

### WorkBuddy

`workbuddy_models()` writes both routes from `ModelRef`:

- `model.name` = `api_model`
- `backend_url_env` = `HARNESS2_WB_BASE_URL`
- `backend_key_env` = `HARNESS2_WB_API_KEY`
- Vertex keeps `backend_key_command` (gcloud token); OpenRouter uses a static
  key env.
- `thought_signature_passback` is set only for Gemini.

`workbuddy.py` passes the resolved URL/key in the subprocess `env` instead of
`HARNESS2_WB_VERTEX_URL`. The web judge route uses the same resolution.
`openrouter.ai` is added to allowed hosts. `preflight` validates generated
routes structurally (provider, api model, backend env) instead of comparing
`"google/" + model`.

### Codex bridge

`harness2-bridge` derives its LiteLLM entry from `ModelRef`:

- Vertex → `vertex_ai/<model>` over the existing ADC path.
- OpenRouter → `openrouter/<vendor>/<model>` with `OPENROUTER_API_KEY`, no
  gcloud requirement.

It prints the same `HARNESS2_CODEX_*` settings. `HARNESS2_CODEX_MODEL` is the
bridge alias so `configure_codex` and WorkBuddy's Codex env stay consistent.
WorkBuddy's Codex `env_override` injects the OpenRouter key instead of
`VERTEX_PROXY_KEY`. The bridge serves `/v1/responses`; unsupported
provider/model combinations fail at startup with a message naming the model ID.

## Backward compatibility

- Vertex configurations keep working unchanged: same env names, route shape,
  and ADC checks.
- Explicit `HARNESS2_JUDGE_*` overrides take precedence over provider inference.
- Unprefixed model IDs keep today's OpenCode semantics.
- Run identity and resume are unaffected; models are already recorded in run
  metadata.

## Error handling

- `ConfigError` messages name the missing variable and the provider, e.g.
  `OPENROUTER_API_KEY is required for OpenRouter executor/judge; set it in .env`.
- `preflight` fails before any paid call: key presence for OpenRouter, ADC only
  for Vertex, WorkBuddy route freshness checked structurally.
- The LAB judge patch raises a clear error when `openrouter/` is used without a
  key, instead of silently using the Mistral fallback.
- The bridge refuses to start on an unsupported provider/model combination.

## Testing

- Unit tests for `resolve_model` / `judge_target`: Vertex, OpenRouter, unknown
  provider, override precedence, missing key.
- Tests for generated WorkBuddy route payloads, bridge configuration, and
  `validate_models` / `preflight` behavior per provider (no network).
- Existing `tests/test_workflow.py` must pass, plus repository lint/typecheck
  commands.
- Live smoke test: one OpenCode executor call and one judge call against an
  OpenRouter model, a WorkBuddy dry-run route check, and one bridge
  `/v1/responses` request. No full benchmark sweep.

## Out of scope

- Baking in default OpenRouter model IDs.
- Changing any benchmark task data or scoring logic beyond judge provider
  plumbing.
- Supporting non-OpenAI-compatible protocols on OpenRouter.
