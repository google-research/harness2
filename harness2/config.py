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

import itertools
import os
import re
import sys
import threading
from pathlib import Path
from typing import Mapping

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


SOLVER_MODEL_OC = env("HARNESS2_SOLVER_MODEL", "")
SOLVER_MODEL_CX = env("HARNESS2_CODEX_MODEL", SOLVER_MODEL_OC.split("/", 1)[-1])
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
LAB_JUDGE_MODEL = (
    "vertex/" + JUDGE_MODEL if JUDGE_MODEL.startswith(("claude", "gemini")) else JUDGE_MODEL
)
LAB_DELIV_CAP = 60000
JB_JUDGE_PYTHON = jb_repo() / ".venv" / "bin" / "python"
JB_JUDGE_MODEL = (
    "google/" + JUDGE_MODEL
    if JUDGE_MODEL.startswith("gemini") and not env("HARNESS2_JUDGE_BASE_URL")
    else JUDGE_MODEL
)
_vertex_host = (
    "aiplatform.googleapis.com"
    if VERTEX_REGION == "global"
    else f"{VERTEX_REGION}-aiplatform.googleapis.com"
)
VERTEX_OPENAI_URL = (
    f"https://{_vertex_host}/v1/projects/{VERTEX_PROJECT}"
    f"/locations/{VERTEX_REGION}/endpoints/openapi"
)
JB_JUDGE_API_BASE = env("HARNESS2_JUDGE_BASE_URL", VERTEX_OPENAI_URL if VERTEX_PROJECT else "")
JB_JUDGE_API_KEY = env("HARNESS2_JUDGE_API_KEY", "")
JB_JUDGE_REASONING_EFFORT = "low"
JB_REPORTING_JUDGE_DETAILS = ""
JB_OBS_TRUNC = 50000

CODEX_VERSION = "0.144.6"
CODEX_DIST = str(DEPENDENCIES / "codex" / "dist")
CODEX_BASE_URL = env("HARNESS2_CODEX_BASE_URL", "")
CODEX_CONTAINER_URL = env("HARNESS2_CODEX_CONTAINER_URL", CODEX_BASE_URL)
VERTEX_PROXY_KEY = env("HARNESS2_CODEX_API_KEY", "")
CODEX_HOME_DIR = DEPENDENCIES / "codex" / "home"

# Pinned from the npm "latest" channel; releases:
# https://www.npmjs.com/package/@anthropic-ai/claude-code?activeTab=versions
CLAUDE_VERSION = "2.1.302"
CLAUDE_BIN = DEPENDENCIES / "claude" / "node_modules" / ".bin" / "claude"
CLAUDE_MODEL = env("HARNESS2_CLAUDE_MODEL", "")
# How Claude Code (the cc executor and the claude_cli proposer) authenticates:
# vertex (default; Application Default Credentials and GOOGLE_CLOUD_PROJECT),
# api_key (ANTHROPIC_API_KEY) or oauth (CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`).
CLAUDE_AUTH_MODES = ("vertex", "api_key", "oauth")
CLAUDE_AUTH = env("HARNESS2_CLAUDE_AUTH", "vertex")
_CLAUDE_AUTH_SECRET = {"api_key": "ANTHROPIC_API_KEY", "oauth": "CLAUDE_CODE_OAUTH_TOKEN"}
# The selected Claude credential leaves os.environ here, so no copied environment (OpenCode, Pi and
# Codex executors, benchmark runners, judges) carries it; claude_auth_env() alone hands it on. An
# ANTHROPIC_API_KEY is left alone unless api_key mode claims it: OpenCode's anthropic provider reads it.
_CLAUDE_SECRETS = {
    name: os.environ.pop(name, "")
    for mode, name in _CLAUDE_AUTH_SECRET.items()
    if mode == "oauth" or mode == CLAUDE_AUTH
}
# Optional, oauth mode only: HARNESS2_CLAUDE_OAUTH_TOKENS, comma-separated `claude setup-token`
# tokens, is used in turn, one token per Claude Code run. A token whose run reports a usage limit
# is skipped for the rest of the process. Unset, the one CLAUDE_CODE_OAUTH_TOKEN is used as before.
_CLAUDE_OAUTH_POOL = [
    t.strip() for t in os.environ.pop("HARNESS2_CLAUDE_OAUTH_TOKENS", "").split(",") if t.strip()
]
_CLAUDE_LIMITED: set[str] = set()
_CLAUDE_TURN = itertools.count()
_CLAUDE_POOL_LOCK = threading.Lock()
_CLAUDE_LIMIT_RE = re.compile(r"You've hit your [\w ]*limit")


def claude_usage_limited(text: str, run_env: Mapping[str, str]) -> bool:
    """True when `text` (a run's output) reports a usage limit for a pooled token; that token,
    the one in `run_env`, is then skipped. Always False without HARNESS2_CLAUDE_OAUTH_TOKENS."""
    token = run_env.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if token not in _CLAUDE_OAUTH_POOL or not _CLAUDE_LIMIT_RE.search(text):
        return False
    with _CLAUDE_POOL_LOCK:
        _CLAUDE_LIMITED.add(token)
    return True


def claude_auth_env() -> dict[str, str]:
    """The env Claude Code needs for CLAUDE_AUTH; ConfigError when its credential is missing."""
    if CLAUDE_AUTH not in CLAUDE_AUTH_MODES:
        raise ConfigError(
            f"HARNESS2_CLAUDE_AUTH={CLAUDE_AUTH!r}; use one of {', '.join(CLAUDE_AUTH_MODES)}"
        )
    if CLAUDE_AUTH == "vertex":
        if not VERTEX_PROJECT:
            raise ConfigError("Set GOOGLE_CLOUD_PROJECT for Claude Code on Vertex AI")
        return {
            "CLAUDE_CODE_USE_VERTEX": "1",
            "ANTHROPIC_VERTEX_PROJECT_ID": VERTEX_PROJECT,
            "CLOUD_ML_REGION": VERTEX_REGION,
        }
    name = _CLAUDE_AUTH_SECRET[CLAUDE_AUTH]
    if CLAUDE_AUTH == "oauth" and _CLAUDE_OAUTH_POOL:
        with _CLAUDE_POOL_LOCK:
            live = [t for t in _CLAUDE_OAUTH_POOL if t not in _CLAUDE_LIMITED]
            if not live:
                raise ConfigError("Every token in HARNESS2_CLAUDE_OAUTH_TOKENS hit its usage limit")
            return {name: live[next(_CLAUDE_TURN) % len(live)]}
    if _CLAUDE_SECRETS.get(name):
        return {name: _CLAUDE_SECRETS[name]}
    return {name: require(name, needed_by=f"HARNESS2_CLAUDE_AUTH={CLAUDE_AUTH}")}


# Pinned from the npm "latest" channel; releases:
# https://www.npmjs.com/package/@earendil-works/pi-coding-agent?activeTab=versions
PI_VERSION = "1.3.0"
PI_BIN = DEPENDENCIES / "pi" / "node_modules" / ".bin" / "pi"
PI_MODEL = env("HARNESS2_PI_MODEL", SOLVER_MODEL_OC)  # same `provider/model` form as OpenCode

WB_SOLVER_MODEL = "harness2/solver"
WB_WEB_JUDGE = "harness2/judge"
WB_RENDER_PY = sys.executable
WB_OC_HARNESS_VER = "1.14.18"
WB_CX_HARNESS_VER = CODEX_VERSION
WB_MAX_JOBS = {"oc": 1, "cx": 1}
WB_MAX_TRIALS = {"oc": 2, "cx": 2}
WB_MODEL_CONNECTION = "local_proxy"

# claude_cli runs the improver and selector in Claude Code (bare Anthropic model ID, auth from
# HARNESS2_CLAUDE_AUTH); with --substrate cc it removes the OpenCode dependency altogether.
DEFAULT_PROPOSER_CHANNEL = env("HARNESS2_PROPOSER_CHANNEL", "opencode")
PROPOSER_MODEL_BY_CHANNEL = {
    "opencode": IMPROVER_MODEL,
    "claude_cli": env("HARNESS2_IMPROVER_MODEL", CLAUDE_MODEL),
}
PHASE_WIDTH = 2
TRANSPORT_ATTEMPTS = 2
PROPOSER_CONCURRENCY = 2


def claude_only(substrate: str = "") -> bool:
    """Claude Code executes and Claude Code improves: no OpenCode model is used."""
    return substrate == "cc" and DEFAULT_PROPOSER_CHANNEL == "claude_cli"


def validate_models(*, judge: bool = False, substrate: str = "") -> None:
    if claude_only(substrate):
        require("HARNESS2_CLAUDE_MODEL", needed_by="the Claude Code executor")
        if "/" in PROPOSER_MODEL_BY_CHANNEL["claude_cli"]:
            raise ConfigError("The claude_cli improver takes a bare Anthropic model ID")
        claude_auth_env()
        if judge:
            require("HARNESS2_JUDGE_MODEL", needed_by="benchmark judging")
        return
    require("HARNESS2_SOLVER_MODEL", needed_by="executor rollouts")
    if "/" not in SOLVER_MODEL_OC or "/" not in IMPROVER_MODEL:
        raise ConfigError("Executor and improver models must use OpenCode's provider/model syntax")
    if any(m.startswith("google-vertex") for m in (SOLVER_MODEL_OC, IMPROVER_MODEL)):
        if not VERTEX_PROJECT:
            raise ConfigError("Set GOOGLE_CLOUD_PROJECT for Vertex AI")
    if judge:
        require("HARNESS2_JUDGE_MODEL", needed_by="benchmark judging")
