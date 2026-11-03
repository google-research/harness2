# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Claude Code as a solver substrate.

The editable tree stays native to Claude Code: CLAUDE.md, project skills,
project agents, scripts and project hooks. Login mode uses the installed CLI's
saved credentials; Vertex mode uses a temporary configuration directory.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest

CLAUDE_SURFACE = SurfaceSpec(
    name="claude",
    always_on=("CLAUDE.md",),
    always_on_max=8_000,
    always_on_cap_reason="CLAUDE.md is loaded at session start and consumes context on every run",
    skill_dir=".claude/skills",
    skill_name_rule="overrides_dir",
    trigger_hint=(
        'Fix it by adding a trigger line naming `{name}` to CLAUDE.md, e.g. "When '
        '<the situation this skill handles>, use the `{name}` skill."'
    ),
    autotrigger_file="CLAUDE.md",
    trigger_line="- When {desc}, use the `{name}` skill.\n",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file=".claude/settings.json",
    hook_events=frozenset(
        {
            "PreToolUse",
            "PostToolUse",
            "PermissionRequest",
            "Notification",
            "UserPromptSubmit",
            "SessionStart",
            "SessionEnd",
            "Stop",
            "SubagentStart",
            "SubagentStop",
            "PreCompact",
        }
    ),
    reject_empty_always_on=True,
    stubs=(
        (
            "scripts/README.md",
            "<!-- c7: deterministic helpers, run as `python3 scripts/<name>.py`. -->\n",
        ),
        (
            "sub_agents/README.md",
            "<!-- c5: delegated Claude agents. Add sub_agents/<name>/AGENT.md with "
            "frontmatter `description:`. -->\n",
        ),
        (
            ".claude/README.md",
            "<!-- c8: project hooks live in .claude/settings.json under a top-level "
            "`hooks` object. -->\n",
        ),
        (
            ".claude/skills/README.md",
            "<!-- c6: reusable workflows. Add .claude/skills/<name>/SKILL.md with "
            "frontmatter `description:`. -->\n",
        ),
    ),
)

CLAUDE_ROOT_ENTRIES = ("CLAUDE.md", ".claude", "scripts")


def _agent_text(name: str, source: str) -> str:
    """Add Claude's required agent name without rewriting the supplied body."""
    if source.startswith("---\n"):
        frontmatter = source[4 : source.find("\n---", 4)]
        if re.search(r"^name:\s*\S", frontmatter, re.MULTILINE):
            return source
        return f"---\nname: {name}\n" + source[4:]
    return f"---\nname: {name}\ndescription: {name} agent\n---\n{source}"


class ClaudeSubstrate:
    """Claude Code behind the shared substrate interface."""

    name = "cc"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return CLAUDE_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        if spec.install_mode != "claude_root":
            raise ValueError(
                f"install_mode {spec.install_mode!r} is not Claude Code's 'claude_root' mode"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(CLAUDE_SURFACE, tree_dir):
            raise ValueError(f"{tree_dir} is not a Claude harness tree (no CLAUDE.md)")
        staged = Path(tempfile.mkdtemp(prefix="adapt-cc-root-"))
        for entry in CLAUDE_ROOT_ENTRIES:
            src = tree_dir / entry
            if not src.exists():
                continue
            if src.is_dir():
                shutil.copytree(src, staged / entry)
            else:
                shutil.copy2(src, staged / entry)

        # sub_agents is the framework-neutral editable path. Claude discovers
        # project agents as one Markdown file per role under .claude/agents.
        agents = tree_dir / "sub_agents"
        if agents.is_dir():
            target = staged / ".claude" / "agents"
            target.mkdir(parents=True, exist_ok=True)
            for source in sorted(agents.glob("*/AGENT.md")):
                name = source.parent.name
                (target / f"{name}.md").write_text(
                    _agent_text(name, source.read_text(encoding="utf-8")), encoding="utf-8"
                )

        # An evolved harness may control hooks, but never permissions, env, MCP,
        # or other settings that broaden its authority.
        settings = staged / ".claude" / "settings.json"
        if settings.is_file():
            document = json.loads(settings.read_text(encoding="utf-8"))
            settings.write_text(
                json.dumps({"hooks": document["hooks"]}, indent=2) + "\n", encoding="utf-8"
            )

        return Install(
            staging_dir=staged,
            fingerprint=RM.fingerprint(tree_dir),
            extras={
                "install_mode": "claude_root",
                **{key: value for key, value in ctx.items() if not key.startswith("file:")},
            },
        )

    def exec_argv(
        self, prompt: str, workspace: Path, model: str, *, reasoning_effort: str = "high"
    ) -> list[str]:
        return [
            str(config.CLAUDE_BIN),
            "-p",
            prompt,
            "--model",
            model,
            "--effort",
            reasoning_effort,
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--permission-mode",
            "bypassPermissions",
            "--setting-sources",
            "project",
            "--strict-mcp-config",
            "--add-dir",
            str(workspace),
        ]

    def exec_env(
        self, base_env: Mapping[str, str], *, config_dir: Path, model: str | None = None
    ) -> dict[str, str]:
        # Remove provider overrides so login mode uses the CLI's saved login.
        env = config.scrub_env(base_env)
        for key in tuple(env):
            if key.startswith(("ANTHROPIC_", "CLAUDE_CODE_")) or key == "CLAUDECODE":
                env.pop(key, None)
        if config.CLAUDE_AUTH == "login":
            # Keep the normal configuration location so the CLI can find its
            # saved login/keychain. Do not copy or extract credentials.
            return env
        env.update(
            {
                "CLAUDE_CONFIG_DIR": str(config_dir),
                "CLAUDE_CODE_USE_VERTEX": "1",
                "CLOUD_ML_REGION": config.VERTEX_REGION,
            }
        )
        return env

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError("ClaudeSubstrate requires a benchmark-supplied launcher")
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000
    ) -> str:
        return render_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def run_failed(self, stream_path: Path) -> bool:
        """Claude can report an API/session failure independently of process status."""
        for event in reversed(_events(stream_path)):
            if event.get("type") == "result":
                return bool(event.get("is_error")) or event.get("subtype") != "success"
        return True

    def cli_version(self) -> str:
        if not shutil.which(str(config.CLAUDE_BIN)):
            raise RuntimeError("Claude Code is missing; install claude on PATH or set HARNESS2_CLAUDE_BIN")
        try:
            result = subprocess.run(
                [str(config.CLAUDE_BIN), "--version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Claude Code could not be started: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError("Claude Code --version failed")
        return result.stdout.strip()

    def preflight(self) -> None:
        if config.CLAUDE_RUNTIME not in ("system", "managed"):
            raise ValueError("HARNESS2_CLAUDE_RUNTIME must be system or managed")
        if config.CLAUDE_AUTH not in ("login", "vertex"):
            raise ValueError("HARNESS2_CLAUDE_AUTH must be login or vertex")
        if config.CLAUDE_AUTH == "vertex":
            config.require("GOOGLE_CLOUD_PROJECT", needed_by="Claude Code Vertex executor")
            config.require("HARNESS2_CLAUDE_MODEL", needed_by="Claude Code Vertex executor")
        version = self.cli_version()
        if config.CLAUDE_RUNTIME == "managed" and version.split()[0] != config.CLAUDE_VERSION:
            raise RuntimeError(
                f"Claude Code install is stale; expected {config.CLAUDE_VERSION}, got "
                f"{version!r}; rerun harness2-setup jb "
                "--substrate cc"
            )
        result = subprocess.run(
            [str(config.CLAUDE_BIN), "--help"], capture_output=True, text=True, timeout=30
        )
        required = ("--effort", "--output-format", "--no-session-persistence",
                    "--permission-mode", "--setting-sources", "--strict-mcp-config")
        if result.returncode or any(flag not in result.stdout for flag in required):
            raise RuntimeError("Installed Claude Code lacks required noninteractive flags; update claude")
        if config.CLAUDE_AUTH == "login":
            result = subprocess.run(
                [str(config.CLAUDE_BIN), "auth", "status", "--json"],
                env=self.exec_env(config.os.environ, config_dir=Path(".")),
                capture_output=True, text=True, timeout=30,
            )
            try:
                logged_in = json.loads(result.stdout).get("loggedIn") is True
            except (ValueError, AttributeError):
                logged_in = False
            if result.returncode or not logged_in:
                raise RuntimeError("Claude Code is not logged in; run claude auth login")


_TRUNC_FMT = "…(+{n} chars — render truncation)"


def _clip(value, limit: int) -> str:
    text = "" if value is None else str(value).replace("\x00", "")
    return text if limit <= 0 or len(text) <= limit else text[:limit] + _TRUNC_FMT.format(n=len(text) - limit)


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_text(item) for item in value)))
    if isinstance(value, dict):
        for key in ("text", "content", "message"):
            got = _text(value.get(key))
            if got:
                return got
    return ""


def _events(path: Path) -> list[dict]:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [{"type": "_read_error", "message": str(exc)}]
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000) -> str:
    lines: list[str] = []
    for event in _events(path):
        event_type = event.get("type")
        if event_type == "_read_error":
            lines.append(f"[HARNESS] {event.get('message')}")
        elif event_type == "assistant":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            for part in message.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "thinking":
                    lines.append(f"[REASONING] {_clip(part.get('thinking'), obs_trunc)}")
                elif part.get("type") == "text":
                    lines.append(f"[ASSISTANT] {_clip(part.get('text'), obs_trunc)}")
                elif part.get("type") == "tool_use":
                    args = _clip(json.dumps(part.get("input") or {}, ensure_ascii=False), args_trunc)
                    lines.append(f"[ACTION] {part.get('name') or 'tool'}({args})")
        elif event_type == "user":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            for part in message.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tag = "OBSERVATION ERROR" if part.get("is_error") else "OBSERVATION"
                    lines.append(f"[{tag}] {_clip(_text(part.get('content')), obs_trunc)}")
        elif event_type == "system" and event.get("subtype") == "api_retry":
            lines.append(f"[HARNESS] api_retry attempt={event.get('attempt')}: {event.get('error')}")
        elif event_type == "result":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            lines.append(
                f"[HARNESS] result {event.get('subtype')} | turns={event.get('num_turns')} "
                f"tokens in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
                f"cost_usd={event.get('total_cost_usd')}"
            )
            if event.get("is_error"):
                errors = "; ".join(map(str, event.get("errors") or [])) or str(event.get("result"))
                lines.append(f"[ERROR] {_clip(errors, 1000)}")
    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=len(rendered) - max_chars)
    return rendered


__all__ = ["CLAUDE_SURFACE", "ClaudeSubstrate", "render_events"]
