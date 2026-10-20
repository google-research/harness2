# OpenRouter Model Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let all benchmark and substrate roles run on OpenRouter models selected purely through environment variables, while keeping Vertex behavior unchanged.

**Architecture:** A single `ModelRef` resolver in `harness2/config.py` classifies each OpenCode-style model ID and derives provider endpoints, API model names, Codex aliases, and WorkBuddy route payloads. Preflight, installers, the Codex bridge, and the LAB judge patch consume that resolver instead of inspecting prefixes themselves.

**Tech Stack:** Python 3.12, `unittest`, OpenCode, LiteLLM proxy, WorkBuddy-Bench, LAB.

---

## File structure

- Modify: `harness2/config.py` — resolver, backends, judge derivation, validation, credential requirements.
- Modify: `harness2/install.py` — WorkBuddy route generation from the resolver.
- Modify: `harness2/benchmarks/workbuddy.py` — pass resolved backend env to the benchmark subprocess.
- Modify: `harness2/preflight.py` — provider-aware credential and route checks.
- Modify: `harness2/benchmarks/jobbench.py` — non-Vertex judge key handling.
- Modify: `harness2/cli/bridge.py` — provider-aware LiteLLM config.
- Modify: `third_party/patches/harvey-labs-judge-vertex-anthropic.diff` — add an OpenRouter judge provider.
- Modify: `docs/SETUP.md` — document OpenRouter configuration.
- Create: `tests/test_providers.py` — resolver, backend, judge, route, and validation tests.

Run the full suite with `python -m unittest discover -s tests -v` and lint with `ruff check .`.

**Tracking:** create or claim a beads issue before Task 1 and close it after Task 9:

```bash
bd create "OpenRouter model support" --type feature --priority 2 --spec-id docs/superpowers/specs/2026-10-20-openrouter-model-support-design.md
bd update <id> --claim
```

---

### Task 1: Model resolver, backend, and judge derivation

**Files:**
- Modify: `harness2/config.py:19-23` (imports)
- Modify: `harness2/config.py:91-102` (model constants)
- Modify: `harness2/config.py:104-189` (backend/judge helpers and validation)
- Test: `tests/test_providers.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_providers.py`:

```python
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Provider resolution and OpenRouter derivation contracts."""

import os
import unittest
from unittest.mock import patch

from harness2 import config


class ResolverTests(unittest.TestCase):
    def test_openrouter_splits_provider_from_api_model(self):
        ref = config.resolve_model("openrouter/anthropic/claude-sonnet-4")
        self.assertEqual(ref.provider, "openrouter")
        self.assertEqual(ref.opencode_id, "openrouter/anthropic/claude-sonnet-4")
        self.assertEqual(ref.api_model, "anthropic/claude-sonnet-4")
        self.assertTrue(ref.is_openrouter)
        self.assertFalse(ref.is_vertex)

    def test_vertex_splits_provider_from_api_model(self):
        ref = config.resolve_model("google-vertex/gemini-3.5-flash")
        self.assertEqual(ref.provider, "google-vertex")
        self.assertEqual(ref.api_model, "gemini-3.5-flash")
        self.assertTrue(ref.is_vertex)

    def test_unknown_provider_keeps_opencode_id(self):
        ref = config.resolve_model("openai/gpt-5")
        self.assertEqual(ref.provider, "openai")
        self.assertEqual(ref.opencode_id, "openai/gpt-5")
        self.assertEqual(ref.api_model, "gpt-5")

    def test_bare_id_is_preserved(self):
        ref = config.resolve_model("gemini-3.5-flash")
        self.assertEqual(ref.provider, "")
        self.assertEqual(ref.opencode_id, "gemini-3.5-flash")
        self.assertEqual(ref.api_model, "gemini-3.5-flash")

    def test_codex_alias_keeps_openrouter_prefix(self):
        self.assertEqual(
            config.codex_alias(config.resolve_model("openrouter/openai/gpt-5")),
            "openrouter/openai/gpt-5",
        )
        self.assertEqual(
            config.codex_alias(config.resolve_model("google-vertex/gemini-3.5-flash")),
            "gemini-3.5-flash",
        )


class BackendTests(unittest.TestCase):
    def test_openrouter_backend(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            backend = config.model_backend("openrouter/anthropic/claude-sonnet-4")
        self.assertEqual(backend.url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(backend.api_model, "anthropic/claude-sonnet-4")
        self.assertEqual(backend.key, "or-key")
        self.assertEqual(backend.key_command, "")
        self.assertFalse(backend.thought_signature)

    def test_openrouter_backend_requires_key(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
            with self.assertRaisesRegex(config.ConfigError, "OPENROUTER_API_KEY"):
                config.model_backend("openrouter/anthropic/claude-sonnet-4")

    def test_vertex_backend_uses_gcloud_and_thought_signatures(self):
        with patch.object(config, "VERTEX_PROJECT", "proj"), patch.object(
            config, "VERTEX_OPENAI_URL", "https://vertex.example/v1"
        ):
            backend = config.model_backend("google-vertex/gemini-3.5-flash")
        self.assertEqual(backend.url, "https://vertex.example/v1/chat/completions")
        self.assertEqual(backend.api_model, "google/gemini-3.5-flash")
        self.assertEqual(backend.key, "")
        self.assertEqual(backend.key_command, config.GCLOUD_TOKEN_COMMAND)
        self.assertTrue(backend.thought_signature)

    def test_unsupported_backend_provider_is_rejected(self):
        with self.assertRaisesRegex(config.ConfigError, "Unsupported backend provider"):
            config.model_backend("openai/gpt-5")


class JudgeTests(unittest.TestCase):
    def test_openrouter_judge_target(self):
        with patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "or-key", "HARNESS2_JUDGE_BASE_URL": "", "HARNESS2_JUDGE_API_KEY": ""},
            clear=False,
        ):
            model = "openrouter/openai/gpt-5"
            self.assertEqual(config.judge_api_base(model), "https://openrouter.ai/api/v1")
            self.assertEqual(config.judge_api_key(model), "or-key")
            self.assertEqual(config.jobbench_judge_model(model), "openai/gpt-5")
            self.assertEqual(config.lab_judge_model(model), model)

    def test_explicit_judge_override_wins(self):
        with patch.dict(
            os.environ,
            {
                "HARNESS2_JUDGE_BASE_URL": "https://proxy.example/v1",
                "HARNESS2_JUDGE_API_KEY": "explicit-key",
            },
            clear=False,
        ):
            model = "openrouter/openai/gpt-5"
            self.assertEqual(config.judge_api_base(model), "https://proxy.example/v1")
            self.assertEqual(config.judge_api_key(model), "explicit-key")

    def test_legacy_bare_gemini_judge_still_maps_to_vertex(self):
        with patch.dict(os.environ, {"HARNESS2_JUDGE_BASE_URL": ""}, clear=False):
            self.assertEqual(
                config.lab_judge_model("gemini-3.5-flash"), "vertex/gemini-3.5-flash"
            )
            self.assertEqual(
                config.jobbench_judge_model("gemini-3.5-flash"),
                "google/gemini-3.5-flash",
            )

    def test_litellm_model_mapping(self):
        self.assertEqual(
            config.litellm_model(config.resolve_model("openrouter/anthropic/claude-sonnet-4")),
            "openrouter/anthropic/claude-sonnet-4",
        )
        self.assertEqual(
            config.litellm_model(config.resolve_model("google-vertex/gemini-3.5-flash")),
            "vertex_ai/gemini-3.5-flash",
        )
        with self.assertRaisesRegex(config.ConfigError, "Unsupported Responses API provider"):
            config.litellm_model(config.resolve_model("openai/gpt-5"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_providers -v`
Expected: FAIL with `AttributeError: module 'harness2.config' has no attribute 'resolve_model'`.

- [ ] **Step 3: Add the resolver and backend helpers**

In `harness2/config.py`, add `dataclass` to the imports:

```python
from dataclasses import dataclass
```

After the `VERTEX_*` block (`config.py:92-96`) and before the model constants, insert:

```python
GCLOUD_TOKEN_COMMAND = "gcloud auth application-default print-access-token"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class ModelRef:
    """One configured model, split into the IDs each consumer needs."""

    provider: str
    opencode_id: str
    api_model: str

    @property
    def is_vertex(self) -> bool:
        return self.provider == "google-vertex"

    @property
    def is_openrouter(self) -> bool:
        return self.provider == "openrouter"


def resolve_model(model: str) -> ModelRef:
    """Split an OpenCode provider/model id; never reject an unknown provider here."""
    value = (model or "").strip()
    provider, sep, rest = value.partition("/")
    if not sep:
        return ModelRef("", value, value)
    return ModelRef(provider, value, rest)


def uses_vertex_credentials(ref: ModelRef) -> bool:
    """Bare Claude/Gemini judge ids are the historical Vertex shorthand."""
    return ref.is_vertex or (
        not ref.provider and ref.api_model.startswith(("claude", "gemini"))
    )


def codex_alias(ref: ModelRef) -> str:
    """The model name Codex sends; the bridge maps it to a provider route."""
    return ref.opencode_id if ref.is_openrouter else ref.api_model


@dataclass(frozen=True)
class Backend:
    url: str
    api_model: str
    key: str = ""
    key_command: str = ""
    thought_signature: bool = False


def model_backend(model: str) -> Backend:
    ref = resolve_model(model)
    if ref.is_openrouter:
        key = env("OPENROUTER_API_KEY", "")
        if not key:
            raise ConfigError(
                "OPENROUTER_API_KEY is required for OpenRouter models; "
                "set it in .env or the shell"
            )
        return Backend(OPENROUTER_BASE_URL + "/chat/completions", ref.api_model, key=key)
    if ref.is_vertex:
        if not VERTEX_PROJECT:
            raise ConfigError("Set GOOGLE_CLOUD_PROJECT for Vertex AI")
        return Backend(
            VERTEX_OPENAI_URL + "/chat/completions",
            "google/" + ref.api_model,
            key_command=GCLOUD_TOKEN_COMMAND,
            thought_signature=ref.api_model.startswith("gemini"),
        )
    raise ConfigError(
        f"Unsupported backend provider {ref.provider or '(none)'!r} for model {model!r}; "
        "use google-vertex/... or openrouter/..."
    )


def litellm_model(ref: ModelRef) -> str:
    if ref.is_vertex:
        return "vertex_ai/" + ref.api_model
    if ref.is_openrouter:
        return ref.opencode_id
    raise ConfigError(
        f"Unsupported Responses API provider {ref.provider or '(none)'!r} for "
        f"model {ref.opencode_id!r}; use google-vertex/... or openrouter/..."
    )


def _judge_value(model: str | None) -> str:
    return JUDGE_MODEL if model is None else model


def judge_api_base(model: str | None = None) -> str:
    ref = resolve_model(_judge_value(model))
    explicit = env("HARNESS2_JUDGE_BASE_URL")
    if explicit:
        return explicit
    if ref.is_openrouter:
        return OPENROUTER_BASE_URL
    return VERTEX_OPENAI_URL if VERTEX_PROJECT else ""


def judge_api_key(model: str | None = None) -> str:
    explicit = env("HARNESS2_JUDGE_API_KEY")
    if explicit:
        return explicit
    ref = resolve_model(_judge_value(model))
    return env("OPENROUTER_API_KEY", "") if ref.is_openrouter else ""


def jobbench_judge_model(model: str | None = None) -> str:
    value = _judge_value(model)
    ref = resolve_model(value)
    if ref.is_openrouter:
        return ref.api_model
    if (
        uses_vertex_credentials(ref)
        and ref.api_model.startswith("gemini")
        and not env("HARNESS2_JUDGE_BASE_URL")
    ):
        return "google/" + ref.api_model
    return value


def lab_judge_model(model: str | None = None) -> str:
    value = _judge_value(model)
    ref = resolve_model(value)
    if ref.is_openrouter:
        return value
    if uses_vertex_credentials(ref):
        return "vertex/" + ref.api_model
    return value
```

- [ ] **Step 4: Rewire the derived constants**

Replace `SOLVER_MODEL_CX` at `config.py:100`:

```python
SOLVER_MODEL_OC = env("HARNESS2_SOLVER_MODEL", "")
_SOLVER_REF = resolve_model(SOLVER_MODEL_OC) if SOLVER_MODEL_OC else None
SOLVER_MODEL_CX = env(
    "HARNESS2_CODEX_MODEL",
    codex_alias(_SOLVER_REF) if _SOLVER_REF else "",
)
```

Replace `LAB_JUDGE_MODEL` at `config.py:133-135`:

```python
LAB_JUDGE_MODEL = lab_judge_model() if JUDGE_MODEL else ""
```

Replace `JB_JUDGE_MODEL` at `config.py:138-142`:

```python
JB_JUDGE_MODEL = jobbench_judge_model() if JUDGE_MODEL else ""
```

Replace `JB_JUDGE_API_BASE` and `JB_JUDGE_API_KEY` at `config.py:152-153`:

```python
JB_JUDGE_API_BASE = judge_api_base()
JB_JUDGE_API_KEY = judge_api_key()
```

- [ ] **Step 5: Replace `validate_models`**

Replace the whole function at `config.py:181-189`:

```python
def validate_models(
    *,
    judge: bool = False,
    solver: str | None = None,
    improver: str | None = None,
    judge_model: str | None = None,
) -> None:
    solver = SOLVER_MODEL_OC if solver is None else solver
    improver = IMPROVER_MODEL if improver is None else improver
    require("HARNESS2_SOLVER_MODEL", needed_by="executor rollouts")
    if "/" not in solver or "/" not in improver:
        raise ConfigError(
            "Executor and improver models must use OpenCode's provider/model syntax"
        )
    refs = [resolve_model(solver), resolve_model(improver)]
    if judge:
        require("HARNESS2_JUDGE_MODEL", needed_by="benchmark judging")
        value = JUDGE_MODEL if judge_model is None else judge_model
        if value:
            refs.append(resolve_model(value))
    if any(r.is_vertex for r in refs) and not VERTEX_PROJECT:
        raise ConfigError("Set GOOGLE_CLOUD_PROJECT for Vertex AI")
    missing = [
        r.opencode_id for r in refs if r.is_openrouter and not env("OPENROUTER_API_KEY")
    ]
    if missing:
        raise ConfigError(
            "OPENROUTER_API_KEY is required for OpenRouter model(s) "
            f"{', '.join(missing)}; set it in .env or the shell"
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m unittest tests.test_providers -v`
Expected: PASS for all tests in `ResolverTests`, `BackendTests`, and `JudgeTests`.

- [ ] **Step 7: Commit**

```bash
git add harness2/config.py tests/test_providers.py
git commit -m "feat: add provider-aware model resolution"
```

---

### Task 2: Credential requirements and validation tests

**Files:**
- Modify: `harness2/config.py` (append `credential_requirements`)
- Test: `tests/test_providers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers.py`:

```python
class ValidationTests(unittest.TestCase):
    def test_openrouter_roles_require_the_key(self):
        with patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "", "HARNESS2_SOLVER_MODEL": "openrouter/openai/gpt-5"},
            clear=False,
        ):
            with self.assertRaisesRegex(config.ConfigError, "OPENROUTER_API_KEY"):
                config.validate_models(
                    solver="openrouter/openai/gpt-5",
                    improver="openrouter/openai/gpt-5",
                )

    def test_openrouter_roles_validate_with_the_key(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "or-key",
                "HARNESS2_SOLVER_MODEL": "openrouter/openai/gpt-5",
                "HARNESS2_JUDGE_MODEL": "openrouter/openai/gpt-5-mini",
            },
            clear=False,
        ):
            config.validate_models(
                solver="openrouter/openai/gpt-5",
                improver="openrouter/anthropic/claude-sonnet-4",
                judge=True,
                judge_model="openrouter/openai/gpt-5-mini",
            )

    def test_vertex_roles_require_a_project(self):
        with patch.dict(
            os.environ,
            {"HARNESS2_SOLVER_MODEL": "google-vertex/gemini-3.5-flash"},
            clear=False,
        ), patch.object(config, "VERTEX_PROJECT", ""):
            with self.assertRaisesRegex(config.ConfigError, "GOOGLE_CLOUD_PROJECT"):
                config.validate_models(
                    solver="google-vertex/gemini-3.5-flash",
                    improver="google-vertex/gemini-3.5-flash",
                )

    def test_credential_requirements_only_include_configured_roles(self):
        with patch.object(
            config, "SOLVER_MODEL_OC", "openrouter/openai/gpt-5"
        ), patch.object(
            config, "IMPROVER_MODEL", "openrouter/openai/gpt-5"
        ), patch.object(config, "JUDGE_MODEL", "gemini-3.5-flash"):
            self.assertEqual(
                config.credential_requirements(judge=True),
                {"vertex": True, "openrouter": True},
            )
            self.assertEqual(
                config.credential_requirements(judge=False),
                {"vertex": False, "openrouter": True},
            )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_providers.ValidationTests -v`
Expected: FAIL with `AttributeError: module 'harness2.config' has no attribute 'credential_requirements'`.

- [ ] **Step 3: Add `credential_requirements`**

Append to `harness2/config.py`, after `validate_models`:

```python
def credential_requirements(
    *,
    judge: bool = False,
    workbuddy: bool = False,
    solver: str | None = None,
    improver: str | None = None,
    judge_model: str | None = None,
) -> dict[str, bool]:
    """Which cloud credentials the configured roles actually need."""
    refs = [
        resolve_model(SOLVER_MODEL_OC if solver is None else solver),
        resolve_model(IMPROVER_MODEL if improver is None else improver),
    ]
    judge_ref = None
    if judge or workbuddy:
        value = JUDGE_MODEL if judge_model is None else judge_model
        if value:
            judge_ref = resolve_model(value)
    return {
        "vertex": any(r.is_vertex for r in refs)
        or bool(judge_ref and uses_vertex_credentials(judge_ref)),
        "openrouter": any(r.is_openrouter for r in refs)
        or bool(judge_ref and judge_ref.is_openrouter),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest tests.test_providers -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness2/config.py tests/test_providers.py
git commit -m "feat: derive credential requirements per provider"
```

---

### Task 3: WorkBuddy route generation and backend env

**Files:**
- Modify: `harness2/install.py:151-190`
- Modify: `harness2/benchmarks/workbuddy.py:620-624`
- Test: `tests/test_providers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers.py`:

```python
class WorkBuddyRouteTests(unittest.TestCase):
    def test_openrouter_solver_route(self):
        from harness2 import install

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            payload = install.workbuddy_route(
                "solver", model="openrouter/anthropic/claude-sonnet-4"
            )
        model = payload["model"]
        self.assertEqual(model["name"], "anthropic/claude-sonnet-4")
        self.assertEqual(model["backend_url_env"], "HARNESS2_WB_SOLVER_URL")
        self.assertEqual(model["backend_key_env"], "HARNESS2_WB_SOLVER_KEY")
        self.assertNotIn("backend_key_command", model)
        self.assertNotIn("thought_signature_passback", model)

    def test_vertex_judge_route_keeps_dynamic_key(self):
        from harness2 import install

        with patch.object(config, "VERTEX_PROJECT", "proj"), patch.object(
            config, "VERTEX_OPENAI_URL", "https://vertex.example/v1"
        ):
            payload = install.workbuddy_route(
                "judge", model="google-vertex/gemini-3.5-flash"
            )
        model = payload["model"]
        self.assertEqual(model["name"], "google/gemini-3.5-flash")
        self.assertEqual(model["backend_url_env"], "HARNESS2_WB_JUDGE_URL")
        self.assertEqual(model["backend_key_command"], config.GCLOUD_TOKEN_COMMAND)
        self.assertTrue(model["thought_signature_passback"])

    def test_workbuddy_env_carries_static_keys_only(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False):
            env = config.workbuddy_env(
                solver="openrouter/openai/gpt-5",
                judge="openrouter/anthropic/claude-sonnet-4",
            )
        self.assertEqual(
            env["HARNESS2_WB_SOLVER_URL"],
            "https://openrouter.ai/api/v1/chat/completions",
        )
        self.assertEqual(env["HARNESS2_WB_SOLVER_KEY"], "or-key")
        self.assertEqual(env["HARNESS2_WB_JUDGE_KEY"], "or-key")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_providers.WorkBuddyRouteTests -v`
Expected: FAIL with `AttributeError: module 'harness2.install' has no attribute 'workbuddy_route'`.

- [ ] **Step 3: Replace `workbuddy_models`**

Replace `harness2/install.py:151-190` with:

```python
def workbuddy_route(role: str, *, model: str | None = None) -> dict:
    """The generated WorkBuddy route for one role, from the configured model."""
    if role not in ("solver", "judge"):
        raise ValueError(f"role must be 'solver' or 'judge', got {role!r}")
    value = model or (config.SOLVER_MODEL_OC if role == "solver" else config.JUDGE_MODEL)
    backend = config.model_backend(value)
    env_prefix = f"HARNESS2_WB_{role.upper()}"
    route = {
        "name": backend.api_model,
        "protocols": ["openai"],
        "backend_url_env": f"{env_prefix}_URL",
        "params": {
            "max_output_tokens": 64000 if role == "solver" else 16384,
            "extra_body": {"reasoning_effort": "high" if role == "solver" else "low"},
        },
    }
    if backend.key_command:
        route["backend_key_command"] = backend.key_command
        route["backend_key_ttl"] = 1800
    else:
        route["backend_key_env"] = f"{env_prefix}_KEY"
    if backend.thought_signature:
        route["thought_signature_passback"] = True
    return {"model": route}


def workbuddy_models(*, dry_run=False):
    """Generate the solver and judge routes from the configured model IDs."""
    try:
        payloads = {role: workbuddy_route(role) for role in ("solver", "judge")}
    except config.ConfigError as exc:
        if not dry_run:
            raise
        print(f"Configure WorkBuddy solver and judge routes -- {exc}", flush=True)
        return
    for role, payload in payloads.items():
        path = config.wb_repo() / "configs" / "models" / "harness2" / f"{role}.yaml"
        print(f"Configure WorkBuddy {role}: {payload['model']['name']}", flush=True)
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Add `workbuddy_env` to `config.py`**

Append after `credential_requirements`:

```python
def workbuddy_env(
    *, solver: str | None = None, judge: str | None = None
) -> dict[str, str]:
    """The URL/key environment every WorkBuddy proxy route reads."""
    result: dict[str, str] = {}
    for role, value in (
        ("SOLVER", SOLVER_MODEL_OC if solver is None else solver),
        ("JUDGE", JUDGE_MODEL if judge is None else judge),
    ):
        backend = model_backend(value)
        result[f"HARNESS2_WB_{role}_URL"] = backend.url
        if backend.key:
            result[f"HARNESS2_WB_{role}_KEY"] = backend.key
    return result
```

- [ ] **Step 5: Pass the resolved env to the WorkBuddy subprocess**

In `harness2/benchmarks/workbuddy.py`, replace the `env` dict at lines 620-624:

```python
        env = {
            **os.environ,
            "NO_FORCE_BUILD": "1",
            **config.workbuddy_env(),
        }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m unittest tests.test_providers -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add harness2/config.py harness2/install.py harness2/benchmarks/workbuddy.py tests/test_providers.py
git commit -m "feat: generate WorkBuddy routes from provider-aware models"
```

---

### Task 4: Provider-aware preflight and JobBench judge key handling

**Files:**
- Modify: `harness2/preflight.py:28-145`
- Modify: `harness2/benchmarks/jobbench.py:1085-1105`
- Test: `tests/test_providers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_providers.py`:

```python
class WorkBuddyPreflightTests(unittest.TestCase):
    def test_route_mismatch_is_reported(self):
        import json
        import tempfile
        from pathlib import Path

        from harness2 import preflight

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=False
        ), patch.object(config, "SOLVER_MODEL_OC", "openrouter/openai/gpt-5"):
            path = Path(tmp) / "configs" / "models" / "harness2" / "solver.yaml"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"model": {"name": "wrong"}}))
            errors = preflight.workbuddy_route_errors(Path(tmp), "solver")
        self.assertTrue(any("stale" in error for error in errors))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_providers.WorkBuddyPreflightTests -v`
Expected: FAIL with `AttributeError: module 'harness2.preflight' has no attribute 'workbuddy_route_errors'`.

- [ ] **Step 3: Add the route-error helper**

In `harness2/preflight.py`, change the install import:

```python
from .install import PATCHES, missing_runners, workbuddy_route
```

Add this helper above `check`:

```python
def workbuddy_route_errors(repo, role: str) -> list[str]:
    """Why one generated WorkBuddy route no longer matches configuration."""
    path = repo / "configs/models/harness2" / f"{role}.yaml"
    try:
        actual = json.loads(path.read_text())["model"]
        expected = workbuddy_route(role)["model"]
    except (OSError, ValueError, KeyError, TypeError):
        return [f"WorkBuddy {role} configuration missing; rerun setup"]
    except config.ConfigError as exc:
        return [str(exc)]
    if actual != expected:
        return [f"WorkBuddy {role} configuration is stale; rerun setup"]
    return []
```

- [ ] **Step 4: Use the helper in the WorkBuddy check**

Replace `preflight.py:133-142` with:

```python
        for role in ("solver", "judge"):
            errors.extend(workbuddy_route_errors(repo, role))
```

- [ ] **Step 5: Rewrite the credential probe**

Move the `bench = adapter.spec.name.split("-", 1)[0]` line above the credential block, then replace `preflight.py:80-85`:

```python
    needs = config.credential_requirements(judge=judge, workbuddy=bench == "wb")
    if needs["vertex"]:
        probe(
            "Google Application Default Credentials",
            ["gcloud", "auth", "application-default", "print-access-token"],
        )
```

- [ ] **Step 6: Replace the JobBench judge key rule**

Replace `preflight.py:104-115`:

```python
        if not config.JB_JUDGE_API_BASE:
            errors.append(
                "Set GOOGLE_CLOUD_PROJECT or HARNESS2_JUDGE_BASE_URL for JobBench judging"
            )
        judge_ref = (
            config.resolve_model(config.JUDGE_MODEL) if config.JUDGE_MODEL else None
        )
        if (
            judge_ref is not None
            and not config.JB_JUDGE_API_KEY
            and not config.uses_vertex_credentials(judge_ref)
        ):
            errors.append(
                "A non-Vertex JobBench judge requires HARNESS2_JUDGE_BASE_URL "
                "and HARNESS2_JUDGE_API_KEY"
            )
```

- [ ] **Step 7: Fix the JobBench judge key fallback**

In `harness2/benchmarks/jobbench.py`, replace the token block at lines 1088-1105:

```python
        api_base = config.JB_JUDGE_API_BASE
        token = config.JB_JUDGE_API_KEY
        judge_ref = (
            config.resolve_model(config.JUDGE_MODEL) if config.JUDGE_MODEL else None
        )
        if not token and judge_ref is not None and config.uses_vertex_credentials(judge_ref):
            try:
                token = subprocess.run(
                    ["gcloud", "auth", "application-default", "print-access-token"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                ).stdout.strip()
            except (subprocess.SubprocessError, OSError):
                token = ""
        if not token:
            print(
                "  [jb] no judge API key (set HARNESS2_JUDGE_API_KEY); judge not run",
                flush=True,
            )
            return False
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m unittest discover -s tests -v`
Expected: PASS for the full suite.

- [ ] **Step 9: Commit**

```bash
git add harness2/preflight.py harness2/benchmarks/jobbench.py tests/test_providers.py
git commit -m "feat: make preflight and JobBench judging provider-aware"
```

---

### Task 5: Provider-aware Codex bridge

**Files:**
- Modify: `harness2/cli/bridge.py:15-108`
- Test: `tests/test_providers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_providers.py`:

```python
class BridgeTests(unittest.TestCase):
    def test_openrouter_bridge_params(self):
        from harness2.cli import bridge

        ref = config.resolve_model("openrouter/anthropic/claude-sonnet-4")
        params = bridge.litellm_params(ref)
        self.assertEqual(params["model"], "openrouter/anthropic/claude-sonnet-4")
        self.assertEqual(params["api_key"], "os.environ/OPENROUTER_API_KEY")

    def test_vertex_bridge_params(self):
        from harness2.cli import bridge

        ref = config.resolve_model("google-vertex/gemini-3.5-flash")
        params = bridge.litellm_params(ref)
        self.assertEqual(params["model"], "vertex_ai/gemini-3.5-flash")
        self.assertEqual(params["vertex_project"], "os.environ/GOOGLE_CLOUD_PROJECT")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_providers.BridgeTests -v`
Expected: FAIL with `AttributeError: module 'harness2.cli.bridge' has no attribute 'litellm_params'`.

- [ ] **Step 3: Replace the LiteLLM config and add `litellm_params`**

At the top of `harness2/cli/bridge.py`, replace `LITELLM_CONFIG` (lines 29-39) with:

```python
LITELLM_CONFIG = """model_list:
  - model_name: {alias}
    litellm_params:
{params}
litellm_settings:
  drop_params: true
general_settings:
  master_key: os.environ/HARNESS2_BRIDGE_KEY
"""


def litellm_params(ref: config.ModelRef) -> dict[str, str]:
    """The provider-specific LiteLLM route for one configured model."""
    if ref.is_vertex:
        return {
            "model": config.litellm_model(ref),
            "vertex_project": "os.environ/GOOGLE_CLOUD_PROJECT",
            "vertex_location": "os.environ/GOOGLE_CLOUD_LOCATION",
        }
    if ref.is_openrouter:
        return {
            "model": config.litellm_model(ref),
            "api_key": "os.environ/OPENROUTER_API_KEY",
        }
    raise config.ConfigError(
        f"Unsupported Responses API provider {ref.provider or '(none)'!r} for "
        f"model {ref.opencode_id!r}; use google-vertex/... or openrouter/..."
    )


def _params_yaml(params: dict[str, str]) -> str:
    return "\n".join(f"      {key}: {value}" for key, value in params.items())
```

- [ ] **Step 4: Rewrite the argument default and provider checks in `main`**

Replace lines 64-104 of `harness2/cli/bridge.py` with:

```python
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--model",
        default=config.SOLVER_MODEL_CX,
        help="model to serve; a bare id is treated as google-vertex/",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface to bind; use 0.0.0.0 so WorkBuddy containers can reach it",
    )
    p.add_argument("--port", type=int, default=0, help="default: the saved port, else a free one")
    args = p.parse_args(argv)

    raw = args.model if "/" in args.model else "google-vertex/" + args.model
    ref = config.resolve_model(raw)
    alias = config.codex_alias(ref)
    params = litellm_params(ref)

    litellm = Path(sys.executable).with_name("litellm")
    if not litellm.is_file():
        p.exit(1, "LiteLLM is not installed; run: uv pip install -e '.[render,bridge]'\n")
    if ref.is_vertex and not config.VERTEX_PROJECT:
        p.exit(1, "Set GOOGLE_CLOUD_PROJECT in .env\n")
    if ref.is_openrouter and not config.env("OPENROUTER_API_KEY"):
        p.exit(1, "Set OPENROUTER_API_KEY in .env\n")

    root = config.DEPENDENCIES / "codex"
    endpoint = _endpoint(root / "bridge.json", args.host, args.port)
    port = endpoint["port"]
    litellm_config = root / "bridge.yaml"
    litellm_config.write_text(
        LITELLM_CONFIG.format(alias=alias, params=_params_yaml(params)),
        encoding="utf-8",
    )

    settings = {
        "HARNESS2_CODEX_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "HARNESS2_CODEX_API_KEY": endpoint["key"],
        "HARNESS2_CODEX_MODEL": alias,
    }
    if args.host != "127.0.0.1":
        settings["HARNESS2_CODEX_CONTAINER_URL"] = f"http://host.docker.internal:{port}/v1"
    print("Add these to .env, then run harness2-setup <bench> --substrate cx:")
    print("\n".join(f"{name}={value}" for name, value in settings.items()), flush=True)

    env = {**os.environ, "HARNESS2_BRIDGE_KEY": endpoint["key"]}
    if ref.is_vertex:
        env["GOOGLE_CLOUD_PROJECT"] = config.VERTEX_PROJECT
        env["GOOGLE_CLOUD_LOCATION"] = config.VERTEX_REGION
    command = [str(litellm), "--config", str(litellm_config), "--host", args.host]
    os.execve(str(litellm), [*command, "--port", str(port)], env)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m unittest tests.test_providers -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add harness2/cli/bridge.py tests/test_providers.py
git commit -m "feat: serve OpenRouter models through the Codex bridge"
```

---

### Task 6: LAB judge OpenRouter provider

**Files:**
- Modify: `third_party/patches/harvey-labs-judge-vertex-anthropic.diff`

- [ ] **Step 1: Apply the existing patch to a scratch clone**

```bash
tmp=$(mktemp -d)
git init -q "$tmp"
git -C "$tmp" remote add origin https://github.com/harveyai/harvey-labs.git
git -C "$tmp" fetch --depth 1 origin b58a28ae9fde5ee4b810e53b4fd0a1915325a35d
git -C "$tmp" checkout -q --detach FETCH_HEAD
git -C "$tmp" apply /Users/astern/dev/harness2/third_party/patches/harvey-labs-judge-vertex-anthropic.diff
```

Expected: no output and exit code 0.

- [ ] **Step 2: Add the OpenRouter provider to the judge**

Run this script with the scratch clone as the working directory:

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("evaluation/judge.py")
s = p.read_text()

old_detect = """    name = model.lower()
    if name.startswith("vertex/gemini"):
"""
new_detect = """    name = model.lower()
    if name.startswith("openrouter/"):
        return "openrouter"
    if name.startswith("vertex/gemini"):
"""
assert old_detect in s
s = s.replace(old_detect, new_detect, 1)

old_model = """        self.model = model.split("/", 1)[1] if self.provider.startswith("vertex-") else model
"""
new_model = """        # 'openrouter/anthropic/claude-x' -> 'anthropic/claude-x'.
        self.model = (
            model.split("/", 1)[1]
            if self.provider.startswith("vertex-") or self.provider == "openrouter"
            else model
        )
"""
assert old_model in s
s = s.replace(old_model, new_model, 1)

old_client = """        elif self.provider == "anthropic":
"""
new_client = """        elif self.provider == "openrouter":
            key = os.environ.get("OPENROUTER_API_KEY")
            if not key:
                raise ValueError(
                    "OPENROUTER_API_KEY is required for an openrouter/ judge model"
                )
            self.client = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1", api_key=key
            )
        elif self.provider == "anthropic":
"""
assert old_client in s
s = s.replace(old_client, new_client, 1)

old_dispatch = """        if self.provider == "openai":
"""
new_dispatch = """        if self.provider in ("openai", "openrouter"):
"""
assert old_dispatch in s
s = s.replace(old_dispatch, new_dispatch, 1)

p.write_text(s)
print("patched")
PY
```

Expected: `patched`.

- [ ] **Step 3: Regenerate the tracked patch**

```bash
git -C "$tmp" diff -- evaluation/judge.py > /Users/astern/dev/harness2/third_party/patches/harvey-labs-judge-vertex-anthropic.diff
```

Expected: the patch now contains `openrouter` in the detector, client, and dispatch hunks.

- [ ] **Step 4: Verify the regenerated patch applies to a fresh checkout**

```bash
check=$(mktemp -d)
git init -q "$check"
git -C "$check" remote add origin https://github.com/harveyai/harvey-labs.git
git -C "$check" fetch --depth 1 origin b58a28ae9fde5ee4b810e53b4fd0a1915325a35d
git -C "$check" checkout -q --detach FETCH_HEAD
git -C "$check" apply --check /Users/astern/dev/harness2/third_party/patches/harvey-labs-judge-vertex-anthropic.diff
rm -rf "$check" "$tmp"
```

Expected: no output and exit code 0.

- [ ] **Step 5: Commit**

```bash
git add third_party/patches/harvey-labs-judge-vertex-anthropic.diff
git commit -m "feat: judge OpenRouter models in LAB"
```

---

### Task 7: Document OpenRouter configuration

**Files:**
- Modify: `docs/SETUP.md:28-53`

- [ ] **Step 1: Add the OpenRouter subsection**

After the existing "Authentication alternatives" section and before "Benchmark installation", insert:

````markdown
### OpenRouter models

OpenRouter uses the same OpenCode-style model IDs as Vertex. Set all three roles
and provide the key:

```bash
OPENROUTER_API_KEY=...
HARNESS2_SOLVER_MODEL=openrouter/anthropic/claude-sonnet-4
HARNESS2_IMPROVER_MODEL=openrouter/anthropic/claude-sonnet-4
HARNESS2_JUDGE_MODEL=openrouter/openai/gpt-5
```

The `openrouter/` prefix is stripped for APIs that take the bare
`<vendor>/<model>` (OpenRouter itself and the JobBench judge); LAB's judge
recognizes the prefix directly. WorkBuddy generates its solver and judge routes
from these IDs, so rerun `harness2-setup wb` after changing either one.

The `claude_cli` proposer channel remains Anthropic/Vertex-only; OpenRouter
improvers use the default `opencode` channel. Explicit
`HARNESS2_JUDGE_BASE_URL` / `HARNESS2_JUDGE_API_KEY` still override provider
inference.

For the Codex executor, install the optional bridge dependencies and serve the
OpenRouter route:

```bash
uv pip install -e '.[render,bridge]'
harness2-bridge --model openrouter/anthropic/claude-sonnet-4
```

Add the printed `HARNESS2_CODEX_*` lines to `.env`, then run setup with
`--substrate cx`. The bridge process holds `OPENROUTER_API_KEY`; WorkBuddy
containers receive only the bridge key.

Vertex settings remain valid unchanged. `OPENROUTER_API_KEY` is required only
when an OpenRouter model is configured.
````

- [ ] **Step 2: Verify the docs render**

Run: `python -m unittest discover -s tests -v`
Expected: PASS (no doc tests, but confirms the tree is still healthy).

- [ ] **Step 3: Commit**

```bash
git add docs/SETUP.md
git commit -m "docs: describe OpenRouter model configuration"
```

---

### Task 8: Live OpenRouter smoke test

Prerequisites: `OPENROUTER_API_KEY` is exported, OpenCode is installed
(`harness2-setup` completed), and the optional bridge dependencies are installed
for the bridge check.

**Files:** none (verification only)

- [ ] **Step 1: Verify the OpenRouter credential and model**

```bash
curl -sS https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  | python3 -c 'import json,sys; ids={m["id"] for m in json.load(sys.stdin)["data"]}; print("model-ok" if "anthropic/claude-sonnet-4" in ids else "model-missing")'
```

Expected: `model-ok`.

- [ ] **Step 2: Verify one OpenCode executor call**

```bash
OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
HARNESS2_SOLVER_MODEL=openrouter/anthropic/claude-sonnet-4 \
python3 - <<'PY'
import tempfile
from harness2.core.proposer import Proposer
from harness2.core.spec import resolve_channel_model

prop = Proposer(resolve_channel_model("opencode", "openrouter/anthropic/claude-sonnet-4"))
reply = prop.call("Reply with the single word READY.", None, "openrouter-smoke")
print(reply.text.strip()[:200])
PY
```

Expected: a non-empty reply containing `READY`.

- [ ] **Step 3: Verify one JobBench-style judge call**

```bash
python3 - <<'PY'
import json, os, urllib.request
from harness2 import config

model = "openrouter/anthropic/claude-sonnet-4"
body = json.dumps({
    "model": config.jobbench_judge_model(model),
    "messages": [{"role": "user", "content": "Reply with the single word OK."}],
    "max_tokens": 8,
}).encode()
req = urllib.request.Request(
    config.judge_api_base(model) + "/chat/completions",
    data=body,
    headers={
        "Authorization": "Bearer " + config.judge_api_key(model),
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(req, timeout=120) as r:
    print(json.loads(r.read())["choices"][0]["message"]["content"])
PY
```

Expected: `OK`.

- [ ] **Step 4: Verify the WorkBuddy route dry-run**

```bash
OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
HARNESS2_SOLVER_MODEL=openrouter/anthropic/claude-sonnet-4 \
HARNESS2_JUDGE_MODEL=openrouter/openai/gpt-5-mini \
harness2-setup wb --dry-run
```

Expected: two lines beginning `Configure WorkBuddy solver:` and
`Configure WorkBuddy judge:`, naming the bare OpenRouter model IDs.

- [ ] **Step 5: Verify the Codex bridge Responses endpoint**

```bash
harness2-bridge --model openrouter/anthropic/claude-sonnet-4 --port 4010 &
bridge_pid=$!
sleep 5
curl -sS http://127.0.0.1:4010/v1/responses \
  -H "Authorization: Bearer $(python3 -c 'import json; print(json.load(open("dependencies/codex/bridge.json"))["key"])')" \
  -H "Content-Type: application/json" \
  -d '{"model":"openrouter/anthropic/claude-sonnet-4","input":"Reply with the single word OK."}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("bridge-ok" if d.get("output") else d)'
kill "$bridge_pid"
```

Expected: `bridge-ok`.

- [ ] **Step 6: Record the result**

If any smoke step fails, file a beads issue with `bd create` describing the
provider and error, and do not claim completion.

---

### Task 9: Full verification and cleanup

**Files:** none

- [ ] **Step 1: Run the full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: PASS, no failures or errors.

- [ ] **Step 2: Run lint**

Run: `ruff check .`
Expected: `All checks passed!` or no output.

- [ ] **Step 3: Review the diff for accidental scope**

Run: `git diff --stat HEAD~7..HEAD` and `git status --short`
Expected: only the files listed in this plan changed; no `.env`, credentials,
or generated route files are tracked.

- [ ] **Step 4: Push**

```bash
git pull --rebase
bd dolt push
git push
git status
```

Expected: `git status` reports the branch is up to date with `origin`.

---

## Self-review notes

- Spec coverage: resolver and validation (Tasks 1-2), WorkBuddy routes and env
  (Task 3), preflight and JobBench judging (Task 4), Codex bridge (Task 5), LAB
  judge (Task 6), docs (Task 7), live smoke test and offline tests (Tasks 8-9).
- No default OpenRouter model IDs are introduced; all roles stay environment
  driven.
- `claude_cli` is explicitly documented as Anthropic/Vertex-only.
- WorkBuddy route env names are role-specific so a Vertex solver and an
  OpenRouter judge can coexist.
