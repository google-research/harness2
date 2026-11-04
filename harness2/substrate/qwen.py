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

"""Qwen Code's editable surface, isolated CLI profile and stream-JSON contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest

PROFILE_VERSION = 1
PROTOCOL_VERSION = 1
BASE_PROMPT = (
    "Follow the task instructions in the task folder and write final deliverables "
    "to the specified output directory.\n"
)
EXCLUDED_TOOLS = (
    "agent",
    "list_agents",
    "send_message",
    "task_stop",
    "cron_create",
    "cron_delete",
    "cron_list",
    "loop_wakeup",
    "monitor",
    "get_goal",
    "update_goal",
    "enter_worktree",
    "exit_worktree",
)
QWEN_SURFACE = SurfaceSpec(
    name="qwen",
    always_on=(
        "systemprompt.md",
        "guardrails.md",
        "LongTermMEMORY.md",
        "tool_descriptions/tool_guidance.md",
    ),
    always_on_max=24_000,
    always_on_cap_reason="supplemental instructions share the executor's context budget",
    skill_dir=".qwen/skills",
    skill_name_rule="must_match",
    trigger_hint="Name the skill and its trigger in guardrails.md.",
    autotrigger_file="guardrails.md",
    trigger_line="- When {desc}, use the skill tool to load {name}.\n",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file=".qwen/hooks.json",
    hook_events=frozenset({"PreToolUse", "PostToolUse"}),
    compile_skill_scripts=True,
    stubs=(
        (
            ".qwen/skills/README.md",
            "Add <name>/SKILL.md with name and description frontmatter; name its trigger "
            "in guardrails.md.\n",
        ),
        ("scripts/README.md", "Deterministic helpers: python3 scripts/<name>.py.\n"),
    ),
)
_POLICY_ENV = ("QWEN_CODE_SYSTEM_SETTINGS_PATH", "QWEN_CODE_SYSTEM_DEFAULTS_PATH")
_OS_ENV = (
    "HOME",
    "USER",
    "LOGNAME",
    "PATH",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def redact(text: str) -> str:
    return text.replace(config.QWEN_API_KEY, "[REDACTED]") if config.QWEN_API_KEY else text


def endpoint() -> str:
    value = config.QWEN_BASE_URL
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise config.ConfigError("HARNESS2_QWEN_BASE_URL must be a valid HTTP(S) URL") from None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(c.isspace() for c in value)
    ):
        raise config.ConfigError(
            "HARNESS2_QWEN_BASE_URL must be HTTP(S), without userinfo, query or fragment"
        )
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None:
        host += f":{port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def policy_identity() -> list[dict]:
    """Only inert system UI/telemetry settings are supported by this initial profile."""
    base_env = os.environ
    for name in _POLICY_ENV:
        if base_env.get(name) and not Path(base_env[name]).is_absolute():
            raise config.ConfigError(f"{name} must be an absolute policy path")
    default = (
        Path("/Library/Application Support/QwenCode/settings.json")
        if sys.platform == "darwin"
        else Path("/etc/qwen-code/settings.json")
    )
    settings = Path(base_env.get(_POLICY_ENV[0]) or default).expanduser().resolve()
    defaults = (
        Path(base_env.get(_POLICY_ENV[1]) or settings.with_name("system-defaults.json"))
        .expanduser()
        .resolve()
    )
    identity = []
    for path, required in (
        (defaults, bool(base_env.get(_POLICY_ENV[1]))),
        (settings, bool(base_env.get(_POLICY_ENV[0]))),
    ):
        try:
            body = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if required:
                raise config.ConfigError(
                    f"Configured Qwen system policy is absent: {path}"
                ) from None
            identity.append({"path": str(path), "sha256": None})
            continue
        try:
            doc = json.loads(body)
        except ValueError:
            raise config.ConfigError(
                f"Cannot establish Qwen policy at {path}: phase 1 requires strict JSON"
            ) from None
        if not isinstance(doc, dict) or set(doc) - {"ui", "telemetry"}:
            raise config.ConfigError(
                f"Qwen system policy at {path} changes runtime behavior; this executor "
                "profile only supports empty or UI/telemetry-only system policy. "
                "The policy was not overridden."
            )
        identity.append({"path": str(path), "sha256": hashlib.sha256(body.encode()).hexdigest()})
    return identity


def _base_env() -> dict[str, str]:
    return {
        **{key: os.environ[key] for key in (*_OS_ENV, *_POLICY_ENV) if key in os.environ},
        "NO_COLOR": "1",
        "CI": "1",
        "OTEL_SDK_DISABLED": "true",
    }


def home_env_identity() -> list[dict]:
    """Qwen always reads legacy home dotenv files, even with a separate QWEN_HOME."""
    identity = []
    for path in (Path.home() / ".env", Path.home() / ".qwen/.env"):
        try:
            body = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            body = None
        if body is not None and any(
            line.strip() and not line.lstrip().startswith("#") for line in body.splitlines()
        ):
            raise config.ConfigError(
                f"Qwen would load personal environment settings from {path}; "
                "use a clean benchmark account. This profile does not import personal dotenv files."
            )
        identity.append({"path": str(path), "sha256": digest(body)})
    return identity


def installed_identity() -> dict:
    root = config.QWEN_DIR
    package = root / "node_modules/@qwen-code/qwen-code"
    remedy = "run harness2-setup jb --substrate qw"
    try:
        metadata = json.loads((package / "package.json").read_text())
        lock = json.loads((root / "package-lock.json").read_text())
        entry = lock["packages"]["node_modules/@qwen-code/qwen-code"]
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError(f"Qwen installation is missing or invalid; {remedy}") from None
    if (
        metadata.get("version") != config.QWEN_VERSION
        or entry.get("integrity") != config.QWEN_INTEGRITY
    ):
        raise RuntimeError(f"Qwen installation differs from the supported package; {remedy}")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Qwen Code requires Node.js >=22 on PATH")
    try:
        node_version = subprocess.check_output(
            [node, "--version"], env=_base_env(), text=True, stderr=subprocess.PIPE, timeout=15
        ).strip()
        if int(node_version.lstrip("v").split(".")[0]) < 22:
            raise RuntimeError("Qwen Code requires Node.js >=22 on PATH")
        with tempfile.TemporaryDirectory(prefix="harness2-qwen-check-") as temporary:
            env = {**_base_env(), "QWEN_HOME": temporary}
            binary = root / "node_modules/.bin/qwen"
            version = subprocess.check_output(
                [str(binary), "--version"], env=env, text=True, stderr=subprocess.PIPE, timeout=30
            ).strip()
            help_text = subprocess.check_output(
                [str(binary), "--help"], env=env, text=True, stderr=subprocess.PIPE, timeout=30
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        raise RuntimeError(f"Cannot run the pinned Qwen CLI with Node >=22; {remedy}") from None
    required = (
        "--append-system-prompt",
        "--output-format",
        "--approval-mode",
        "--max-wall-time",
        "--chat-recording",
        "--exclude-tools",
        "--auth-type",
        "--openai-base-url",
    )
    if version != config.QWEN_VERSION or any(flag not in help_text for flag in required):
        raise RuntimeError(f"Qwen CLI version/flags differ from the supported contract; {remedy}")
    return {
        "version": version,
        "integrity": entry["integrity"],
        "node": node_version,
        "package_fingerprint": RM.fingerprint(package),
    }


def validate_tree(tree: HT.Tree) -> None:
    HT.validate(QWEN_SURFACE, tree, allow_subagents=False, reserved_scripts=frozenset())
    for rel, body in tree.items():
        if not HT.is_allowed(QWEN_SURFACE, rel):
            raise ValueError(f"{rel} is outside the Qwen editable surface")
        if rel.endswith("/SKILL.md"):
            frontmatter = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|$)", body, re.S)
            if not frontmatter or set(
                re.findall(r"^([A-Za-z][\w-]*):", frontmatter.group(1), re.M)
            ) - {"name", "description"}:
                raise ValueError(f"{rel}: phase 1 allows only name/description frontmatter")
    src = tree.get(".qwen/hooks.json")
    if src is None:
        return
    doc = json.loads(src)
    if set(doc) != {"hooks"}:
        raise ValueError("Qwen hooks source may contain only the hooks object")
    for entries in doc["hooks"].values():
        for entry in entries:
            if set(entry) - {"matcher", "hooks"} or not isinstance(entry.get("matcher", "*"), str):
                raise ValueError("Qwen hook entries allow only a matcher string and hooks list")
            for hook in entry["hooks"]:
                timeout = hook.get("timeout", 10)
                if set(hook) - {"type", "command", "timeout"} or (
                    isinstance(timeout, bool)
                    or not isinstance(timeout, (int, float))
                    or not 1 <= timeout <= 60
                ):
                    raise ValueError(
                        "Qwen command hooks require a synchronous 1..60 second timeout"
                    )


class QwenSubstrate:
    name = "qw"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return QWEN_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        if spec.install_mode != "qwen_root":
            raise ValueError("QwenSubstrate requires install_mode='qwen_root'")
        tree_dir = Path(tree_dir)
        rejected = []
        tree = HT.read_tree(QWEN_SURFACE, tree_dir, rejected=rejected)
        if rejected or any(path.is_symlink() for path in tree_dir.rglob("*")):
            raise ValueError("Qwen harness contains off-surface files or symbolic links")
        if not HT.is_tree(QWEN_SURFACE, tree_dir):
            raise ValueError("Qwen harness has no always-on source file")
        validate_tree(tree)
        staged = Path(tempfile.mkdtemp(prefix="harness2-qwen-install-"))
        try:
            for rel, body in tree.items():
                if rel in QWEN_SURFACE.always_on or rel == QWEN_SURFACE.hooks_file:
                    continue
                target = staged / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
            (staged / ".harness2").mkdir()
            (staged / ".qwen").mkdir(exist_ok=True)
            (staged / ".qwen/.env").write_text("", encoding="utf-8")
            (staged / ".harness2/append-system-prompt.md").write_text(
                tree.get("systemprompt.md", ""), encoding="utf-8"
            )
            memory = "\n\n".join(
                f"# {rel}\n\n{tree[rel].strip()}"
                for rel in QWEN_SURFACE.always_on[1:]
                if tree.get(rel, "").strip()
            )
            (staged / "QWEN.md").write_text(memory, encoding="utf-8")
            hooks = json.loads(tree.get(".qwen/hooks.json", '{"hooks": {}}'))["hooks"]
            for entries in hooks.values():
                for entry in entries:
                    for hook in entry["hooks"]:
                        hook.setdefault("timeout", 10)
            settings = {
                "context": {"fileName": "QWEN.md", "loadFromIncludeDirectories": False},
                "skills": {
                    "enabled": HT.skill_names(QWEN_SURFACE, tree),
                    "disabledLevels": ["user", "bundled", "extension"],
                },
                "general": {"chatRecording": False},
                "telemetry": {"enabled": False},
                "hooks": hooks,
            }
            (staged / ".qwen/settings.json").write_text(
                json.dumps(settings, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
            manifest = {"profile": PROFILE_VERSION, "generated": RM.fingerprint(staged)}
            (staged / ".harness2/install.json").write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        return Install(staged, RM.fingerprint(tree_dir), {"install_mode": "qwen_root"})

    def execution_identity(self) -> dict:
        return {
            "substrate": self.name,
            "model": config.QWEN_MODEL,
            "endpoint": endpoint(),
            "auth_type": "openai",
            "installed": installed_identity(),
            "profile": PROFILE_VERSION,
            "protocol": PROTOCOL_VERSION,
            "policy": policy_identity(),
            "home_env": home_env_identity(),
            "excluded_tools": EXCLUDED_TOOLS,
            "baseline": digest(BASE_PROMPT),
        }

    def exec_env(self, *, qwen_home: Path, workspace: Path) -> dict[str, str]:
        home_env_identity()
        qwen_home.mkdir(parents=True, exist_ok=True)
        (qwen_home / "settings.json").write_text(
            json.dumps(
                {
                    "security": {"folderTrust": {"enabled": True}},
                    "telemetry": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )
        (qwen_home / "trustedFolders.json").write_text(
            json.dumps({str(workspace.resolve()): "TRUST_FOLDER"}), encoding="utf-8"
        )
        return {
            **_base_env(),
            "QWEN_HOME": str(qwen_home.resolve()),
            "QWEN_RUNTIME_DIR": str(qwen_home.resolve() / "runtime"),
            "QWEN_CODE_MCP_APPROVALS_PATH": str(qwen_home.resolve() / "mcp-approvals.json"),
            "QWEN_CODE_TRUSTED_FOLDERS_PATH": str(qwen_home.resolve() / "trustedFolders.json"),
            "OPENAI_API_KEY": config.QWEN_API_KEY,
        }

    def exec_argv(self, *, install: Install, timeout: int) -> list[str]:
        argv = [
            str(config.QWEN_DIR / "node_modules/.bin/qwen"),
            "--auth-type",
            "openai",
            "--model",
            config.QWEN_MODEL,
            "--openai-base-url",
            endpoint(),
            "--output-format",
            "stream-json",
            "--approval-mode",
            "yolo",
            "--chat-recording=false",
            "--max-wall-time",
            str(timeout),
            "--sandbox=false",
        ]
        append = (install.staging_dir / ".harness2/append-system-prompt.md").read_text()
        if append.strip():
            argv += ["--append-system-prompt", append]
        for tool in EXCLUDED_TOOLS:
            argv += ["--exclude-tools", tool]
        return argv

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError("QwenSubstrate requires a benchmark-supplied launcher")
        return self._launcher(RunRequest(task_ref, tuple(run_ids), int(timeout)), install)

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000
    ) -> str:
        transcript = parse_events(stream_path)
        lines = []
        for event in transcript.events:
            typ = event.get("type")
            if typ in ("assistant", "user"):
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content", [])
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    kind = block.get("type")
                    if kind == "tool_use":
                        text = f"{block.get('name')} [{block.get('id')}]: {json.dumps(block.get('input'))}"
                        lines.append("[ACTION] " + _clip(text, args_trunc))
                    elif kind == "tool_result":
                        tag = "OBSERVATION ERROR" if block.get("is_error") else "OBSERVATION"
                        text = _block_text(block.get("content", ""))
                        lines.append(
                            f"[{tag}] [{block.get('tool_use_id')}] " + _clip(text, obs_trunc)
                        )
                    elif kind in ("text", "thinking"):
                        tag = "REASONING" if kind == "thinking" else typ.upper()
                        lines.append(f"[{tag}] " + _clip(block.get(kind, ""), obs_trunc))
            elif typ == "system":
                lines.append(f"[HARNESS] {event.get('subtype', 'system')}")
            elif typ == "result":
                lines.append(f"[HARNESS] result: {event.get('subtype')}")
            else:
                lines.append("[HARNESS] " + _clip(json.dumps(event), obs_trunc))
        lines.extend("[HARNESS ERROR] " + error for error in transcript.errors)
        return _clip("\n".join(lines), max_chars)

    def preflight(self) -> None:
        for value, name in (
            (config.QWEN_MODEL, "HARNESS2_QWEN_MODEL"),
            (config.QWEN_BASE_URL, "HARNESS2_QWEN_BASE_URL"),
            (config.QWEN_API_KEY, "HARNESS2_QWEN_API_KEY"),
        ):
            if not value:
                raise config.ConfigError(f"Set {name} for the Qwen Code executor")
        self.execution_identity()


def _clip(text: str, limit: int) -> str:
    text = str(text).replace("\x00", "")
    if limit <= 0 or len(text) <= limit:
        return text
    marker = "… (render truncated)"
    return text[: max(0, limit - len(marker))] + marker[:limit]


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return json.dumps(content, ensure_ascii=False)


@dataclass
class Transcript:
    events: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    result: dict | None = None

    @property
    def ok(self) -> bool:
        return not self.errors and self.result is not None


def parse_events(path: Path) -> Transcript:
    """Validate final messages/tool pairing and completion without relying on JSON key order."""
    parsed = Transcript()
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        parsed.errors.append("Cannot read a UTF-8 Qwen trajectory")
        return parsed
    seen: dict[tuple, dict] = {}
    pending: set[str] = set()
    used: set[str] = set()
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            if line.lstrip().startswith(("{", "[")):
                parsed.errors.append(f"Malformed JSON at line {number}")
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            parsed.errors.append(f"Invalid event at line {number}")
            continue
        typ = event["type"]
        message = event.get("message")
        iid = (message.get("id") if isinstance(message, dict) else None) or event.get("uuid")
        if isinstance(iid, str):
            key = (typ, iid)
            if key in seen:
                if seen[key] != event:
                    parsed.errors.append(f"Conflicting duplicate message at line {number}")
                continue
            seen[key] = event
        if parsed.result is not None and typ in ("assistant", "user", "result"):
            parsed.errors.append("Conversation continued after terminal result")
        parsed.events.append(event)
        if typ in ("assistant", "user"):
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            if not isinstance(content, list) or any(not isinstance(b, dict) for b in content):
                parsed.errors.append(f"Invalid message content at line {number}")
                continue
            for block in content:
                kind = block.get("type")
                if kind == "tool_use":
                    call_id = block.get("id")
                    if (
                        typ != "assistant"
                        or not isinstance(call_id, str)
                        or not call_id
                        or call_id in used
                        or not isinstance(block.get("name"), str)
                        or not isinstance(block.get("input"), dict)
                    ):
                        parsed.errors.append(f"Invalid/repeated tool call at line {number}")
                    else:
                        pending.add(call_id)
                        used.add(call_id)
                elif kind == "tool_result":
                    call_id = block.get("tool_use_id")
                    if typ != "user" or not isinstance(call_id, str) or call_id not in pending:
                        parsed.errors.append(f"Orphan tool result at line {number}")
                    else:
                        pending.remove(call_id)
                    if "is_error" in block and not isinstance(block["is_error"], bool):
                        parsed.errors.append(f"Invalid tool result status at line {number}")
                elif kind in ("text", "thinking"):
                    if not isinstance(block.get(kind), str):
                        parsed.errors.append(f"Invalid text block at line {number}")
                elif kind not in ("text", "thinking", "image"):
                    parsed.errors.append(f"Unsupported conversation block at line {number}")
        elif typ == "system" and isinstance(event.get("tools"), list):
            if any(not isinstance(tool, str) for tool in event["tools"]):
                parsed.errors.append(f"Invalid tool list at line {number}")
            elif set(event["tools"]) & set(EXCLUDED_TOOLS):
                parsed.errors.append("Excluded tools were advertised by Qwen")
            if event.get("mcp_servers"):
                parsed.errors.append("Unexpected MCP servers were loaded by Qwen")
        elif typ == "result":
            parsed.result = event
            if event.get("subtype") != "success" or event.get("is_error") is not False:
                parsed.errors.append("Qwen reported terminal failure")
    if pending:
        parsed.errors.append(f"Unresolved tool calls: {', '.join(sorted(pending))}")
    if parsed.result is None:
        parsed.errors.append("Missing terminal result")
    return parsed
