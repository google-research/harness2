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

"""DeepSeek's official Python SDK, with benchmark-independent workspace execution.

The SDK owns the agent loop. A short-lived worker owns one SDK process so a wall
deadline also bounds startup, a stalled notification stream, tools, and shutdown.
Only an explicit continuation reuses an attempt's workspace, home, and session.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.locking import file_lock
from ..core.tree import SurfaceSpec
from .base import ExecutionError, Install, Launcher, RunRequest

EVENTS = "deepseek_session.jsonl"
MANIFEST = "deepseek_run.json"
ADAPTER_VERSION = 1
_RUNTIME_PLUGIN = Path(__file__).with_name("deepseek_runtime.mjs")

DEEPSEEK_SURFACE = SurfaceSpec(
    name="deepseek",
    always_on=(
        "systemprompt.md",
        "guardrails.md",
        "LongTermMEMORY.md",
        "tool_descriptions/tool_guidance.md",
    ),
    always_on_max=24_000,
    always_on_cap_reason="the whole composition is supplied to every model request",
    skill_dir="skills",
    skill_name_rule="must_match",
    trigger_hint="Name `{name}` in guardrails.md and tell the agent to read its SKILL.md.",
    autotrigger_file="guardrails.md",
    trigger_line="- When {desc}, read skills/{name}/SKILL.md in the harness directory.\n",
    subagent_requires_mode=False,
    plugin_dir="plugins",
    plugin_extension="mjs",
    hooks_file=None,
    compile_skill_scripts=True,
    reject_empty_always_on=True,
    stubs=(
        ("scripts/README.md", "Python helpers; resolve paths relative to the harness directory.\n"),
        (
            "skills/README.md",
            "On-demand manuals: skills/<name>/SKILL.md with name and description frontmatter.\n",
        ),
        (
            "plugins/README.md",
            "Native Cordis ES modules, plugins/<name>.mjs. Export apply(ctx) and inject as needed. "
            "Use ctx.tools.guard or ctx.systemPrompt.section; never log to stdout.\n",
        ),
    ),
)


@dataclass(frozen=True)
class DeepSeekSettings:
    """Deployment controls, never part of the model-editable candidate tree."""

    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com/anthropic"
    provider: str = "deepseek-official"
    profile: str = "sdk-minimal"
    dsh_bin: str | None = None
    api_key_env: str = "DEEPSEEK_API_KEY"
    reasoning_effort: str = "high"
    max_tokens: int = 49_152
    max_model_calls: int = 120
    wall_timeout: int = 3600
    context_window: int = 1_000_000
    shell_timeout: int = 300
    initialize_timeout: int = 30
    shutdown_timeout: int = 5
    max_parallel_tools: int = 1
    text_tools: bool = True

    @classmethod
    def from_env(cls) -> DeepSeekSettings:
        def value(name: str, default):
            return config.env("HARNESS2_DEEPSEEK_" + name, default)

        text_tools = str(value("TEXT_TOOLS", "true")).lower()
        if text_tools not in ("true", "false"):
            raise config.ConfigError("HARNESS2_DEEPSEEK_TEXT_TOOLS must be true or false")
        return cls(
            model=value("MODEL", cls.model),
            base_url=value("BASE_URL", config.env("DEEPSEEK_BASE_URL", cls.base_url)),
            provider=value("PROVIDER", cls.provider),
            profile=value("PROFILE", cls.profile),
            dsh_bin=value("BIN", None),
            api_key_env=value("API_KEY_ENV", cls.api_key_env),
            reasoning_effort=value("REASONING_EFFORT", cls.reasoning_effort),
            text_tools=text_tools == "true",
            **{
                name: config.env_int("HARNESS2_DEEPSEEK_" + name.upper(), getattr(cls, name))
                for name in (
                    "max_tokens",
                    "max_model_calls",
                    "wall_timeout",
                    "context_window",
                    "shell_timeout",
                    "initialize_timeout",
                    "shutdown_timeout",
                    "max_parallel_tools",
                )
            },
        )

    def __post_init__(self):
        if not self.model.strip():
            raise config.ConfigError("HARNESS2_DEEPSEEK_MODEL must not be blank")
        if self.profile != "sdk-minimal" or self.provider != "deepseek-official":
            raise config.ConfigError(
                "This adapter supports sdk-minimal with deepseek-official. Extend it with native "
                "plugins/*.mjs; full sdk profiles and other provider routes need a separate mapping."
            )
        url = urlsplit(self.base_url)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise config.ConfigError(
                "HARNESS2_DEEPSEEK_BASE_URL must be an HTTP(S) Messages API root without "
                "credentials, query, or fragment (not an OpenAI chat/completions endpoint)"
            )
        try:
            url.port
        except ValueError as exc:
            raise config.ConfigError("Invalid HARNESS2_DEEPSEEK_BASE_URL port") from exc
        if self.reasoning_effort not in ("off", "low", "high", "max"):
            raise config.ConfigError("HARNESS2_DEEPSEEK_REASONING_EFFORT: off | low | high | max")
        if type(self.text_tools) is not bool:
            raise config.ConfigError("HARNESS2_DEEPSEEK_TEXT_TOOLS must be a boolean")
        if url.path.rstrip("/").endswith(("/chat/completions", "/responses")):
            raise config.ConfigError(
                "DeepSeek Harness requires a Messages API root, not chat/completions or Responses"
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
            raise config.ConfigError(
                "HARNESS2_DEEPSEEK_API_KEY_ENV must name an environment variable"
            )
        for name, value in asdict(self).items():
            if name in (
                "max_tokens",
                "max_model_calls",
                "wall_timeout",
                "context_window",
                "shell_timeout",
                "initialize_timeout",
                "shutdown_timeout",
                "max_parallel_tools",
            ) and (type(value) is not int or not 0 < value <= 2_147_483):
                raise config.ConfigError(
                    f"HARNESS2_DEEPSEEK_{name.upper()} must be a positive integer"
                )
        if self.context_window <= self.max_tokens + 16_384:
            raise config.ConfigError(
                "HARNESS2_DEEPSEEK_CONTEXT_WINDOW must exceed MAX_TOKENS + 16384 "
                "to leave room for the native compaction headroom"
            )
        if self.dsh_bin:
            binary = Path(self.dsh_bin).expanduser().resolve()
            if not binary.is_file():
                raise config.ConfigError(
                    "HARNESS2_DEEPSEEK_BIN must name an existing dsh executable"
                )
            object.__setattr__(self, "dsh_bin", str(binary))


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def validate_tree(tree: HT.Tree) -> None:
    unsupported = sorted(rel for rel in tree if not HT.is_allowed(DEEPSEEK_SURFACE, rel))
    if unsupported:
        raise ValueError(
            f"Unsupported DeepSeek candidate files: {unsupported}. Use the four always-on "
            "markdown files, skills/*/SKILL.md, scripts/*.py, or native plugins/*.mjs. "
            "OpenCode TypeScript plugins, Codex hooks, and raw profile/config files are not portable."
        )
    if HT.subagent_names(DEEPSEEK_SURFACE, tree):
        raise ValueError(
            "sdk-minimal has no subagent dispatcher: convert sub_agents roles to on-demand "
            "skills, or use oc/cx where delegation is supported. AGENT.md files would be inert."
        )
    HT.validate(DEEPSEEK_SURFACE, tree, allow_subagents=False, reserved_scripts=frozenset())


def _safe_copy(source: Path, dest: Path) -> None:
    """Never dereference an agent-created link into the surrounding host."""
    if _is_link(source) or any(_is_link(p) for p in source.rglob("*")):
        raise ValueError(f"Symlinks are not supported in DeepSeek inputs or artifacts: {source}")
    shutil.copytree(source, dest, dirs_exist_ok=True)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def compose_prompt(staged: Path, harness_root: Path) -> str:
    tree = HT.read_tree(DEEPSEEK_SURFACE, staged)
    parts = [tree.get(name, "").strip() for name in DEEPSEEK_SURFACE.always_on]
    parts.append(
        f"Harness directory: {harness_root}\n"
        "Relative skills/ and scripts/ paths in these instructions refer to that directory, "
        "not the task workspace. Read skill bodies before applying them. There is no skill or "
        "subagent tool in this profile. Use the filesystem tools or the platform shell."
    )
    for name in HT.skill_names(DEEPSEEK_SURFACE, tree):
        rel = f"skills/{name}/SKILL.md"
        desc = re.search(r"^description:\s*(.+)$", tree[rel][:600], re.M)
        parts.append(f"Skill {name}: {desc.group(1) if desc else ''}\nRead: {harness_root / rel}")
    scripts = sorted(rel for rel in tree if rel.endswith(".py"))
    if scripts:
        parts.append("Python helpers:\n" + "\n".join(str(harness_root / rel) for rel in scripts))
    return "\n\n".join(part for part in parts if part)


class DeepSeekSubstrate:
    name = "ds"

    def __init__(
        self,
        launcher: Launcher | None = None,
        *,
        settings: DeepSeekSettings | None = None,
        api_key: str | None = None,
        _worker_command: tuple[str, ...] | None = None,
    ):
        self._launcher = launcher
        self.settings = settings or DeepSeekSettings.from_env()
        self._api_key_override = api_key
        self._worker_command = _worker_command
        self._active: dict[str, threading.Event] = {}
        self._active_lock = threading.Lock()

    def surface(self) -> SurfaceSpec:
        return DEEPSEEK_SURFACE

    def runtime_identity(self) -> dict:
        settings = asdict(self.settings)
        binary = self.settings.dsh_bin
        installed = {}
        for package in ("deepseek-harness-sdk", "deepseek-harness-runtime-bin", "psutil"):
            try:
                installed[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                installed[package] = "not installed"
        return {
            "adapter": ADAPTER_VERSION,
            "sdk": config.DEEPSEEK_VERSION,
            "upstream": config.DEEPSEEK_REVISION,
            "runtime_plugin": _file_digest(_RUNTIME_PLUGIN),
            "installed": installed,
            "platform": [sys.platform, platform.machine(), platform.python_version()],
            "settings": settings,
            "binary_sha256": _file_digest(Path(binary).expanduser()) if binary else None,
            "worker": list(self._worker_command) if self._worker_command else "official-sdk",
        }

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        if spec.install_mode != "deepseek_profile":
            raise ValueError("DeepSeek requires install_mode='deepseek_profile'")
        rejected: list[str] = []
        tree_dir = Path(tree_dir)
        if not HT.is_tree(DEEPSEEK_SURFACE, tree_dir):
            raise ValueError(f"No DeepSeek always-on harness files found at {tree_dir}")
        if _is_link(tree_dir) or any(_is_link(p) for p in tree_dir.rglob("*")):
            raise ValueError("Candidate harnesses must contain regular files, not symlinks")
        tree = HT.read_tree(DEEPSEEK_SURFACE, tree_dir, rejected=rejected)
        if rejected:
            raise ValueError(
                f"Unsupported DeepSeek candidate files: {rejected}; use native plugins/*.mjs, "
                "not OpenCode plugins, Codex hooks, or profile patches."
            )
        for key, body in ctx.items():
            if not key.startswith("file:"):
                raise ValueError(
                    f"DeepSeek has no implicit template slots ({key!r}); pass literal prompt text"
                )
            rel = HT.normalize_rel(key[5:])
            if rel in tree:
                raise ValueError(f"Benchmark integration would overwrite candidate file {rel}")
            tree[rel] = body
        validate_tree(tree)
        staged = Path(tempfile.mkdtemp(prefix="adapt-ds-"))
        try:
            HT.write_tree(DEEPSEEK_SURFACE, tree, staged, stubs=False)
            fingerprint = RM.fingerprint(tree_dir)
            execution = _digest(
                {"source": fingerprint, "tree": tree, "runtime": self.runtime_identity()}
            )
            return Install(
                staged,
                fingerprint,
                {
                    "install_mode": "deepseek_profile",
                    "execution_fingerprint": execution,
                },
            )
        except BaseException:
            shutil.rmtree(staged)
            raise

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "DeepSeekSubstrate needs a benchmark launcher; standalone callers can use "
                "run_workspace(install, run_dir=..., run_id=..., prompt=..., timeout=...)."
            )
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("Independent DeepSeek rollouts require unique run_ids")
        results = []
        for run_id in run_ids:
            paths = self._launcher(RunRequest(task_ref, (run_id,), int(timeout)), install)
            if len(paths) != 1:
                raise RuntimeError(
                    "DeepSeek benchmark launcher must return one directory per rollout"
                )
            results.extend(paths)
        return results

    def _key(self) -> str:
        if self._api_key_override is not None:
            return self._api_key_override
        return str(
            config.env("HARNESS2_DEEPSEEK_API_KEY", config.env(self.settings.api_key_env, ""))
        )

    def preflight(self, *, require_credentials: bool = True) -> None:
        """No profile startup, session, provider discovery, or model request."""
        if require_credentials and not self._key().strip():
            raise config.ConfigError(
                f"Set {self.settings.api_key_env} (or HARNESS2_DEEPSEEK_API_KEY) for DeepSeek"
            )
        try:
            for package in ("deepseek-harness-sdk", "deepseek-harness-runtime-bin"):
                installed = importlib.metadata.version(package)
                if installed != config.DEEPSEEK_VERSION:
                    raise RuntimeError(
                        f"{package}=={installed} is unsupported; install harness2[deepseek] "
                        f"with both SDK and runtime pinned to {config.DEEPSEEK_VERSION}"
                    )
            import psutil  # noqa: F401
            from deepseek_harness_runtime import resolve_bundled_launch_args
        except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
            raise RuntimeError(
                "Install the optional runtime: python -m pip install -e '.[deepseek]'"
            ) from exc
        command = (
            [str(Path(self.settings.dsh_bin).expanduser().resolve())]
            if self.settings.dsh_bin
            else list(resolve_bundled_launch_args())
        )
        try:
            result = subprocess.run(
                command + ["--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                "dsh --version failed; reinstall the pinned DeepSeek runtime"
            ) from exc
        if not re.search(r"\b0\.2\.1(?:-rc\.2|rc2)\b", result.stdout):
            raise RuntimeError(f"Expected dsh 0.2.1-rc.2, got {result.stdout.strip()!r}")

    def _patch(self, bundle: Path, workspace: Path) -> Path:
        settings = self.settings
        shutil.copy2(_RUNTIME_PLUGIN, bundle / "_runtime.mjs")
        rows: list[dict] = [
            {
                "id": "system-prompt",
                "config": {
                    "includeHarnessIdentity": False,
                    "includeRuntimeContext": False,
                    "personaPrefix": "",
                },
            },
            {"id": "session-log-deepseek", "disabled": True},
            {"id": "plugin-package-inventory-deepseek", "disabled": True},
            {
                "id": "llm-deepseek",
                "config": {
                    "apiKeyEnv": "DEEPSEEK_API_KEY",
                    "baseURL": settings.base_url,
                    "defaultContextWindow": settings.context_window,
                    "maxTokens": settings.max_tokens,
                    "streamIdleTimeoutMs": 300_000,
                },
            },
            {
                "id": "agent-loop",
                "config": {
                    "agents": [],
                    "maxParallelToolCalls": settings.max_parallel_tools,
                },
            },
            {
                "id": "tool-fs",
                "config": {
                    "read": settings.text_tools,
                    "write": settings.text_tools,
                    "edit": settings.text_tools,
                    "readImage": True,
                },
            },
        ]
        for row in ("terminal-bash", "terminal-pwsh", "persistent-bash", "persistent-pwsh"):
            native = {"timeoutMs": settings.shell_timeout * 1000}
            if row == "terminal-pwsh":
                native["shellDialect"] = "pwsh"
            rows.append({"id": row, "config": native})
        plugins = [
            {
                "id": "harness2-runtime",
                "name": "./_runtime.mjs",
                "config": {
                    "prompt": compose_prompt(bundle, bundle),
                    "provider": settings.provider,
                    "model": settings.model,
                    "maxModelCalls": settings.max_model_calls,
                },
            },
        ]
        required = ["sdkAppStartup", "loader", "harness2RuntimeReady"]
        for index, path in enumerate(sorted((bundle / "plugins").glob("*.mjs"))):
            service = f"harness2Plugin{index}Ready"
            required.append(service)
            wrapper = bundle / f"_plugin_{index}.mjs"
            module = json.dumps("./" + path.relative_to(bundle).as_posix())
            # Cordis normally warns and skips failed optional plugins. Make the SDK's
            # required server depend on each candidate's successful activation.
            wrapper.write_text(
                f"import * as candidate from {module};\n"
                "export const inject = candidate.inject ?? [];\n"
                "export const Config = candidate.Config;\n"
                "export async function apply(ctx, config) {\n"
                "  if (typeof candidate.apply !== 'function') {\n"
                f"    throw new Error({json.dumps(str(path.name) + ' must export apply(ctx, config)')});\n"
                "  }\n"
                "  const result = await candidate.apply(ctx, config);\n"
                f"  ctx.provide({json.dumps(service)}, {{ ready: true }});\n"
                "  return result;\n"
                "}\n",
                encoding="utf-8",
            )
            plugins.append({"id": f"harness2-plugin-{path.stem}", "name": "./" + wrapper.name})
        rows.append({"id": "sdk-jsonrpc-server", "inject": required})
        rows.append({"insert": plugins})
        patch = bundle / "_profile.json"
        # JSON is a YAML subset; no YAML emitter or !!js interpolation of candidate text.
        patch.write_text(json.dumps(rows, indent=2, ensure_ascii=True), encoding="utf-8")
        return patch

    def _env(self, home: Path, *, native_cache: Path | None = None) -> dict[str, str]:
        process_home = native_cache or home
        keep = (
            "PATH",
            "SystemRoot",
            "SystemDrive",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "PROCESSOR_ARCHITECTURE",
            "PROCESSOR_ARCHITEW6432",
            "NUMBER_OF_PROCESSORS",
            "TEMP",
            "TMP",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
            "NODE_EXTRA_CA_CERTS",
        )
        env = {key: os.environ[key] for key in keep if key in os.environ}
        env.update(
            {
                "HOME": str(process_home),
                "USERPROFILE": str(process_home),
                "DSH_HOME": str(home),
                "XDG_CONFIG_HOME": str(home / "config"),
                "XDG_DATA_HOME": str(home / "data"),
                "XDG_CACHE_HOME": str(process_home / "cache"),
                "APPDATA": str(process_home / "appdata"),
                "LOCALAPPDATA": str(process_home / "localappdata"),
                "PYTHONPATH": str(config.PKG.parent),
                "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "HARNESS2_SKIP_DOTENV": "1",
                "DEEPSEEK_API_KEY": self._key(),
                "DEEPSEEK_BASE_URL": self.settings.base_url,
            }
        )
        return env

    def cancel(self, run_id: str) -> bool:
        """Cancel this instance's active rollout; the caller receives ExecutionError."""
        with self._active_lock:
            event = self._active.get(run_id)
            if event is not None:
                event.set()
            return event is not None

    def run_workspace(
        self,
        install: Install,
        *,
        run_dir: Path,
        run_id: str,
        prompt: str,
        timeout: int,
        inputs: Mapping[str, Path] | None = None,
        provenance: Mapping | None = None,
        tool_python: Path | None = None,
    ) -> Path:
        """Execute or reuse one rollout. Inputs are already answer-free benchmark material."""
        if timeout <= 0:
            raise ValueError("DeepSeek wall timeout must be positive")
        if not run_id or not prompt.strip():
            raise ValueError("DeepSeek requires nonempty run_id and prompt")
        run_dir = Path(run_dir).resolve()
        if tool_python is not None and not tool_python.is_file():
            raise ValueError(
                f"Document-tool interpreter missing at {tool_python}; run harness2-setup for this benchmark"
            )
        inputs = inputs or {}
        for name, source in inputs.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name == "output":
                raise ValueError(f"Invalid workspace input directory {name!r}")
            if not Path(source).is_dir():
                raise ValueError(f"Input directory does not exist: {source}")
        identity = {
            "run_id": run_id,
            "execution": install.extras["execution_fingerprint"],
            "harness": install.fingerprint,
            "runtime": self.runtime_identity(),
            "prompt": _digest(prompt),
            "timeout": timeout,
            "inputs": {name: RM.fingerprint(path) for name, path in inputs.items()},
            "provenance": dict(provenance or {}),
            "tool_python": str(tool_python.resolve()) if tool_python is not None else None,
        }
        with file_lock(run_dir.with_name(run_dir.name + ".lock"), blocking=False):
            old = _json(run_dir / MANIFEST) if (run_dir / MANIFEST).exists() else None
            if (
                old
                and old.get("identity") == identity
                and not old.get("continuations")
                and self.artifacts_intact(run_dir)
            ):
                return run_dir
            if not old and (run_dir / "output").exists():
                raise ValueError(f"Refusing to overwrite unowned output directory at {run_dir}")
            attempt = run_dir / "attempts" / uuid.uuid4().hex
            workspace, home, bundle = attempt / "workspace", attempt / "home", attempt / "harness"
            workspace.mkdir(parents=True)
            home.mkdir(mode=0o700)
            _safe_copy(install.staging_dir, bundle)
            for name, source in inputs.items():
                _safe_copy(Path(source), workspace / name)
            (workspace / "output").mkdir()
            patch = self._patch(bundle, workspace)
            meta = {
                "identity": identity,
                "session_id": "harness2-" + uuid.uuid4().hex,
                "workspace": str(workspace),
                "home": str(home),
                "patch": str(patch),
                "state": "running",
                "continuations": 0,
                "tool_python": identity["tool_python"],
            }
            return self._invoke(run_dir, meta, prompt, timeout, resume=False)

    def continue_session(
        self, install: Install, *, run_dir: Path, prompt: str, timeout: int
    ) -> Path:
        """Opt-in continuation only; candidates with different identities cannot inherit history."""
        run_dir = Path(run_dir).resolve()
        if timeout <= 0 or not prompt.strip():
            raise ValueError("Continuation requires a positive timeout and a nonempty prompt")
        with file_lock(run_dir.with_name(run_dir.name + ".lock"), blocking=False):
            meta = _json(run_dir / MANIFEST)
            identity = meta["identity"]
            if (
                identity["execution"] != install.extras["execution_fingerprint"]
                or identity["runtime"] != self.runtime_identity()
            ):
                raise ValueError(
                    "Continuation requires the same harness, model, and runtime settings"
                )
            for name in ("home", "workspace"):
                if not Path(meta[name]).is_dir():
                    raise ValueError(
                        f"Cannot continue: missing DeepSeek {name}; no replacement created"
                    )
            if not Path(meta["patch"]).is_file():
                raise ValueError("Cannot continue: native profile patch is missing")
            meta["continuations"] += 1
            return self._invoke(run_dir, meta, prompt, timeout, resume=True)

    def _invoke(
        self, run_dir: Path, meta: dict, prompt: str, timeout: int, *, resume: bool
    ) -> Path:
        run_id = meta["identity"]["run_id"]
        with self._active_lock:
            if run_id in self._active:
                raise RuntimeError(f"DeepSeek run_id is already active: {run_id}")
            cancel = self._active[run_id] = threading.Event()
        started = time.monotonic()
        proc = None
        error = ""
        state = "failed"
        metrics = {}
        # The wheel's Windows native-addon cache can exceed MAX_PATH under a long
        # experiment namespace. It is disposable; durable sessions remain in DSH_HOME.
        native_cache = None
        try:
            meta["state"] = "running"
            write_json(run_dir / MANIFEST, meta)
            if not resume:
                (run_dir / EVENTS).write_text("", encoding="utf-8")
            native_cache = tempfile.TemporaryDirectory(prefix="h2ds-native-")
            (run_dir / "metrics.json").unlink(missing_ok=True)
            (run_dir / "final_response.txt").unlink(missing_ok=True)
            request = {
                "settings": asdict(self.settings),
                "session_id": meta["session_id"],
                "workspace": meta["workspace"],
                "home": meta["home"],
                "patch": meta["patch"],
                "run_dir": str(run_dir),
                "prompt": prompt,
                "resume": resume,
            }
            write_json(run_dir / "request.json", request)
            command = list(
                self._worker_command
                or (
                    sys.executable,
                    "-m",
                    "harness2.substrate.deepseek_worker",
                )
            ) + [str(run_dir / "request.json")]
            kwargs = (
                {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                if os.name == "nt"
                else {"start_new_session": True}
            )
            env = self._env(Path(meta["home"]), native_cache=Path(native_cache.name))
            if meta.get("tool_python"):
                env["PATH"] = (
                    str(Path(meta["tool_python"]).parent) + os.pathsep + env.get("PATH", "")
                )
            with (run_dir / "worker.log").open("a", encoding="utf-8") as log:
                proc = subprocess.Popen(
                    command,
                    cwd=meta["workspace"],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    **kwargs,
                )
                while proc.poll() is None:
                    if cancel.wait(0.05):
                        state = "cancelled"
                        raise RuntimeError("DeepSeek rollout cancelled")
                    if time.monotonic() - started >= timeout:
                        state = "timeout"
                        raise TimeoutError(f"DeepSeek wall timeout after {timeout}s")
                if (run_dir / "metrics.json").is_file():
                    metrics = _json(run_dir / "metrics.json")
                if (
                    proc.returncode != 0
                    or metrics.get("finish_reason") != "completed"
                    or metrics.get("session_id") != meta["session_id"]
                ):
                    raise RuntimeError(
                        f"DeepSeek worker exit={proc.returncode}; "
                        f"finish_reason={metrics.get('finish_reason')!r}; "
                        f"{metrics.get('error', 'missing or invalid SDK result')}"
                    )
                state = "completed"
        except (Exception, KeyboardInterrupt) as exc:
            error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, KeyboardInterrupt):
                state = "cancelled"
            if proc is not None and proc.poll() is None:
                stop_process_tree(proc, grace=self.settings.shutdown_timeout + 2)
                if (run_dir / "metrics.json").is_file():
                    try:
                        metrics = _json(run_dir / "metrics.json")
                    except (OSError, ValueError) as metrics_error:
                        error += f"\nCannot read interrupted worker metrics: {metrics_error}"
        finally:
            with self._active_lock:
                self._active.pop(run_id, None)
            try:
                if native_cache is not None:
                    native_cache.cleanup()
            except OSError as exc:
                state = "failed"
                error += f"\nNative cache cleanup failed: {exc}"
            log_path = run_dir / "worker.log"
            if log_path.is_file():
                log_path.write_text(
                    redact(log_path.read_text(encoding="utf-8", errors="replace"), self._key()),
                    encoding="utf-8",
                )
            output = run_dir / "output"
            output_fingerprint = None
            try:
                if output.exists():
                    if _is_link(output):
                        raise ValueError(f"Refusing linked output directory {output}")
                    shutil.rmtree(output)
                _safe_copy(Path(meta["workspace"]) / "output", output)
                output_fingerprint = RM.fingerprint(output)
            except (OSError, ValueError) as exc:
                state = "failed"
                error += f"\nCannot collect deliverables: {exc}"
            metrics.update(
                {
                    "state": state,
                    "wall_s": round(time.monotonic() - started, 3),
                    "error": redact(error, self._key()),
                    "session_id": meta["session_id"],
                    "cost_usd": metrics.get("cost_usd"),
                }
            )
            usage = metrics.get("usage") or {}
            metrics.update(
                {
                    "input_tokens": usage.get("inputTokens"),
                    "output_tokens": usage.get("outputTokens"),
                    "wall_clock_seconds": metrics["wall_s"],
                }
            )
            write_json(run_dir / "metrics.json", metrics)
            meta.update(
                {
                    "state": state,
                    "output_fingerprint": output_fingerprint,
                    "events_sha256": _file_digest(run_dir / EVENTS),
                }
            )
            write_json(run_dir / MANIFEST, meta)
            archive = Path(meta["workspace"]).parent / f"turn-{meta['continuations']}"
            archive.mkdir(exist_ok=True)
            for name in (EVENTS, "metrics.json", "request.json", "worker.log"):
                if (run_dir / name).is_file():
                    shutil.copy2(run_dir / name, archive / name)
        if error:
            tail = (
                (run_dir / "worker.log").read_text(encoding="utf-8", errors="replace")[-4000:]
                if (run_dir / "worker.log").is_file()
                else ""
            )
            raise ExecutionError(
                f"{redact(error + chr(10) + tail, self._key())}\nEvidence: {run_dir}",
                run_dir=run_dir,
            )
        return run_dir

    def artifacts_intact(self, run_dir: Path) -> bool:
        try:
            meta = _json(Path(run_dir) / MANIFEST)
            return (
                meta["state"] == "completed"
                and meta["identity"]["runtime"] == self.runtime_identity()
                and _json(Path(run_dir) / "metrics.json").get("state") == "completed"
                and meta["output_fingerprint"] == RM.fingerprint(Path(run_dir) / "output")
                and meta["events_sha256"] == _file_digest(Path(run_dir) / EVENTS)
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000
    ) -> str:
        return render_events(
            stream_path, obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )


def stop_process_tree(proc: subprocess.Popen, *, grace: int) -> None:
    """Snapshot descendants before the parent exits; never kill by executable name."""
    import psutil

    try:
        children = psutil.Process(proc.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    if proc.poll() is None:
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        except OSError:
            proc.terminate()
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    for child in reversed(children):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(children, timeout=grace)


def redact(text: str, key: str) -> str:
    return text.replace(key, "[REDACTED]") if key else text


def _clip(text: object, limit: int) -> str:
    value = str(text).replace("\0", "")
    if limit > 0 and len(value) > limit:
        return value[:limit] + f"... (+{len(value) - limit} chars; render truncation)"
    return value


def _content(message: object) -> str:
    if not isinstance(message, dict):
        return str(message or "")
    blocks = message.get("content", [])
    if not isinstance(blocks, list):
        return str(blocks)
    return "\n".join(
        str(block.get("text", block.get("thinking", "")))
        if block.get("type") in ("text", "thinking", "reasoning")
        else json.dumps(block, ensure_ascii=False)
        for block in blocks
        if isinstance(block, dict) and block.get("type") != "tool-call"
    )


def event_of(record: dict) -> tuple[str, dict | None]:
    if record.get("method") == "session.event":
        payload = record.get("params", {})
    elif record.get("type") == "session_event":
        payload = record
    else:
        return "", record if "type" in record else None
    if not isinstance(payload, dict):
        raise ValueError("session event payload must be an object")
    event = payload.get("event")
    if not isinstance(event, dict):
        raise ValueError("session event requires an event object")
    return str(payload.get("sessionId", "")), event


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000) -> str:
    lines = []
    seen = set()
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"[ERROR] Cannot read DeepSeek trajectory: {exc}"
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("expected an event object")
            session, event = event_of(record)
            if event is not None and not isinstance(event.get("type"), str):
                raise ValueError("event.type must be a string")
        except ValueError as exc:
            lines.append(f"[ERROR] Invalid DeepSeek JSONL line {number}: {exc}")
            continue
        if event is None:
            if record.get("method") == "harness2.error":
                lines.append(f"[ERROR] {_clip(record.get('params'), obs_trunc)}")
            continue
        if isinstance(event.get("seq"), int):
            key = (session, event["seq"])
            if key in seen:
                continue
            seen.add(key)
        kind, data = event.get("type", ""), event.get("data", {})
        if not isinstance(data, dict):
            lines.append(f"[HARNESS] {kind}: {_clip(data, obs_trunc)}")
            continue
        if kind in ("assistant/message", "user/message", "system/message"):
            tag = {
                "assistant/message": "ASSISTANT",
                "user/message": "USER",
                "system/message": "HARNESS",
            }[kind]
            text = _content(data.get("message", data))
            if text:
                lines.append(f"[{tag}] {_clip(text, obs_trunc)}")
            usage = data.get("usage", event.get("usage"))
            if isinstance(usage, dict):
                lines.append(f"[USAGE] {json.dumps(usage, sort_keys=True)}")
        elif kind == "tool/call":
            lines.append(
                f"[ACTION] {data.get('name')} [{data.get('callId', '')}]: "
                f"{_clip(data.get('arguments', ''), args_trunc)}"
            )
        elif kind == "tool/result":
            message = data.get("message", {})
            failed = data.get("error") or (isinstance(message, dict) and message.get("isError"))
            lines.append(
                f"[OBSERVATION{' ERROR' if failed else ''}] "
                f"{_clip(_content(message), obs_trunc)}"
                + (f" | {_clip(data['error'], obs_trunc)}" if data.get("error") else "")
            )
        elif kind == "turn/end":
            reason = data.get("reason", {})
            tag = (
                "HARNESS"
                if isinstance(reason, dict) and reason.get("kind") == "completed"
                else "ERROR"
            )
            lines.append(f"[{tag}] turn/end: {_clip(reason, obs_trunc)}")
        elif kind not in ("agent/inbox/spliced", "step/start", "step/end", "turn/start"):
            tag = "ERROR" if "error" in kind or "failure" in kind else "HARNESS"
            lines.append(f"[{tag}] {kind}: {_clip(json.dumps(data), obs_trunc)}")
    return _clip("\n".join(lines) or "(no DeepSeek session events)", max_chars)
