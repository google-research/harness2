# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deployment settings and dependency versions. Shell values override .env."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent if (PKG.parent / "pyproject.toml").is_file() else Path.cwd()
THIRD_PARTY = ROOT / "third_party"
METHOD_VERSION = 2

DEPENDENCIES = ROOT / "dependencies"


class ConfigError(RuntimeError):
    """A required deployment value is missing; the message carries the remediation."""


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines from `path` into os.environ with setdefault (the shell wins).

    Accepts blank lines, `#` comments, an optional `export ` prefix, and single- or
    double-quoted values. A missing or unreadable file is not an error.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not all(c.isalnum() or c == "_" for c in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


load_dotenv()


def env(name: str, default=None):
    """Env override where a set-but-empty variable is the same as an unset one."""
    return os.environ.get(name) or default


def env_int(name: str, default: int) -> int:
    v = env(name)
    if v is None:
        return int(default)
    try:
        return int(v)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {v!r}") from e


def require(name: str, *, needed_by: str) -> str:
    """The value of `name`, or ConfigError with the remediation line when it is unset/empty."""
    v = env(name)
    if not v:
        raise ConfigError(
            f"{name} is required for {needed_by}; set it in .env or the shell (see .env.example)"
        )
    return str(v)


RUNS = Path(env("HARNESS2_RUNS_DIR", ROOT / "runs")).expanduser().resolve()
VERTEX_PROJECT = env("GOOGLE_CLOUD_PROJECT", env("HARNESS2_VERTEX_PROJECT", ""))
VERTEX_REGION = env("GOOGLE_CLOUD_LOCATION", "global")
if VERTEX_PROJECT:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", VERTEX_PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", VERTEX_REGION)

GCLOUD_TOKEN_COMMAND = "gcloud auth application-default print-access-token"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_vertex_host = (
    "aiplatform.googleapis.com"
    if VERTEX_REGION == "global"
    else f"{VERTEX_REGION}-aiplatform.googleapis.com"
)
VERTEX_OPENAI_URL = (
    f"https://{_vertex_host}/v1/projects/{VERTEX_PROJECT}"
    f"/locations/{VERTEX_REGION}/endpoints/openapi"
)


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


def resolve_model(model: str | None) -> ModelRef:
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
    """The HTTP endpoint, API model name, and credentials one model talks to."""
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
    """The model name for a LiteLLM Responses API client."""
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
    """The judge's OpenAI-compatible base URL, with explicit overrides first."""
    ref = resolve_model(_judge_value(model))
    explicit = env("HARNESS2_JUDGE_BASE_URL")
    if explicit:
        return explicit
    if ref.is_openrouter:
        return OPENROUTER_BASE_URL
    return VERTEX_OPENAI_URL if VERTEX_PROJECT else ""


def judge_api_key(model: str | None = None) -> str:
    """The judge's API key, with explicit overrides first."""
    explicit = env("HARNESS2_JUDGE_API_KEY")
    if explicit:
        return explicit
    ref = resolve_model(_judge_value(model))
    return env("OPENROUTER_API_KEY", "") if ref.is_openrouter else ""


def jobbench_judge_model(model: str | None = None) -> str:
    """The job-bench judge model name, mapping Vertex shorthand to google/..."""
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
    return ref.opencode_id


def lab_judge_model(model: str | None = None) -> str:
    """The harvey-labs judge model name, mapping Vertex shorthand to vertex/..."""
    value = _judge_value(model)
    ref = resolve_model(value)
    if ref.is_openrouter:
        return ref.opencode_id
    if uses_vertex_credentials(ref):
        return "vertex/" + ref.api_model
    return ref.opencode_id


SOLVER_MODEL_OC = env("HARNESS2_SOLVER_MODEL", "")
SOLVER_MODEL_CX = env("HARNESS2_CODEX_MODEL", codex_alias(resolve_model(SOLVER_MODEL_OC)))
IMPROVER_MODEL = env("HARNESS2_IMPROVER_MODEL", SOLVER_MODEL_OC)
JUDGE_MODEL = env("HARNESS2_JUDGE_MODEL", "")

DEPENDENCY_PINS = {
    "job-bench-eval": "fa03a17f9ff662f136798a274e10011c3aa00fd0",
    "workbuddy-bench": "b516950be5b56eb3be406c2f76ee1c5111dcb57f",
    "harvey-labs": "b58a28ae9fde5ee4b810e53b4fd0a1915325a35d",
    "opencode": "23fb5e0516c99ac04a1aa46c193efda2e1b9bb24",
}
DEPENDENCY_ORIGINS = {
    "job-bench-eval": "https://github.com/Job-Bench/job-bench-eval.git",
    "workbuddy-bench": "https://github.com/Tencent/workbuddy-bench.git",
    "harvey-labs": "https://github.com/harveyai/harvey-labs.git",
    "opencode": "https://github.com/anomalyco/opencode.git",
}


def jb_repo() -> Path:
    return DEPENDENCIES / "job-bench-eval"


def wb_repo() -> Path:
    return DEPENDENCIES / "workbuddy-bench"


def lab_repo() -> Path:
    return DEPENDENCIES / "harvey-labs"


OPENCODE_DIR = DEPENDENCIES / "opencode"
BUN = DEPENDENCIES / "tools" / "node_modules" / ".bin" / "bun"
LAB_PYTHON = lab_repo() / ".venv" / "bin" / "python"
LAB_JUDGE_MODEL = lab_judge_model() if JUDGE_MODEL else ""
LAB_DELIV_CAP = 60000
JB_JUDGE_PYTHON = jb_repo() / ".venv" / "bin" / "python"
JB_JUDGE_MODEL = jobbench_judge_model() if JUDGE_MODEL else ""
JB_JUDGE_API_BASE = judge_api_base()
JB_JUDGE_API_KEY = judge_api_key()
JB_JUDGE_REASONING_EFFORT = "low"
JB_REPORTING_JUDGE_DETAILS = ""
JB_OBS_TRUNC = 50000

CODEX_VERSION = "0.144.6"
CODEX_DIST = str(DEPENDENCIES / "codex" / "dist")
CODEX_BASE_URL = env("HARNESS2_CODEX_BASE_URL", "")
CODEX_CONTAINER_URL = env("HARNESS2_CODEX_CONTAINER_URL", CODEX_BASE_URL)
VERTEX_PROXY_KEY = env("HARNESS2_CODEX_API_KEY", "")
CODEX_HOME_DIR = DEPENDENCIES / "codex" / "home"

WB_SOLVER_MODEL = "harness2/solver"
WB_WEB_JUDGE = "harness2/judge"
WB_RENDER_PY = sys.executable
WB_OC_HARNESS_VER = "1.14.18"
WB_CX_HARNESS_VER = CODEX_VERSION
WB_MAX_JOBS = {"oc": 1, "cx": 1}
WB_MAX_TRIALS = {"oc": 2, "cx": 2}
WB_MODEL_CONNECTION = "local_proxy"

DEFAULT_PROPOSER_CHANNEL = "opencode"
PROPOSER_MODEL_BY_CHANNEL = {"opencode": IMPROVER_MODEL, "claude_cli": IMPROVER_MODEL}
PHASE_WIDTH = 2
TRANSPORT_ATTEMPTS = 2
PROPOSER_CONCURRENCY = 2


def validate_models(
    *,
    judge: bool = False,
    solver: str | None = None,
    improver: str | None = None,
    judge_model: str | None = None,
) -> None:
    if solver is None:
        require("HARNESS2_SOLVER_MODEL", needed_by="executor rollouts")
        solver = SOLVER_MODEL_OC
    improver = IMPROVER_MODEL if improver is None else improver
    if "/" not in solver or "/" not in improver:
        raise ConfigError(
            "Executor and improver models must use OpenCode's provider/model syntax"
        )
    refs = [resolve_model(solver), resolve_model(improver)]
    judge_ref = None
    if judge:
        if judge_model is None:
            require("HARNESS2_JUDGE_MODEL", needed_by="benchmark judging")
            judge_model = JUDGE_MODEL
        if judge_model:
            judge_ref = resolve_model(judge_model)
            refs.append(judge_ref)
    vertex_needed = any(r.is_vertex for r in refs)
    if judge_ref is not None and uses_vertex_credentials(judge_ref):
        vertex_needed = True
    if vertex_needed and not VERTEX_PROJECT:
        raise ConfigError("Set GOOGLE_CLOUD_PROJECT for Vertex AI")
    missing = list(
        dict.fromkeys(
            r.opencode_id for r in refs if r.is_openrouter and not env("OPENROUTER_API_KEY")
        )
    )
    if missing:
        raise ConfigError(
            "OPENROUTER_API_KEY is required for OpenRouter model(s) "
            f"{', '.join(missing)}; set it in .env or the shell"
        )


def credential_requirements(
    *,
    judge: bool = False,
    workbuddy: bool = False,
    solver: str | None = None,
    improver: str | None = None,
    judge_model: str | None = None,
) -> dict[str, bool]:
    """Which cloud credentials the configured roles actually need.

    `judge` includes the judge credentials for benchmark grading, while
    `workbuddy` includes them for WorkBuddy's solver and judge routes. The
    result reports the providers of the configured roles, not whether every
    credential needed for a benchmark is present and valid.
    """
    refs = [
        resolve_model(SOLVER_MODEL_OC if solver is None else solver),
        resolve_model(IMPROVER_MODEL if improver is None else improver),
    ]
    judge_ref = None
    if judge or workbuddy:
        value = _judge_value(judge_model)
        if value:
            judge_ref = resolve_model(value)
    return {
        "vertex": any(r.is_vertex for r in refs)
        or (judge_ref is not None and uses_vertex_credentials(judge_ref)),
        "openrouter": any(r.is_openrouter for r in refs)
        or (judge_ref is not None and judge_ref.is_openrouter),
    }


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
