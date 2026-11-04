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

"""ClaudeSubstrate: Claude Code (config.CLAUDE_VERSION) as a solver substrate.

Carries the c1..c8 surface (CLAUDE_SURFACE), the one install mode (claude_root: CLAUDE.md,
.claude and scripts copied to the workspace root, where `claude -p` run in that directory
finds them), the headless flag contract, and the stream-json trajectory renderer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Mapping

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest, read_events, stage_entries
from .codex import _TRUNC_FMT, _clip, _emit_if, _text_of

CLAUDE_SURFACE = SurfaceSpec(
    name="claude",
    always_on=("CLAUDE.md",),
    always_on_max=8_000,
    always_on_cap_reason="CLAUDE.md is loaded whole into context at the start of EVERY session",
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
            "PostToolUseFailure",
            "UserPromptSubmit",
            "Stop",
            "SubagentStart",
            "SubagentStop",
            "SessionStart",
            "SessionEnd",
            "PreCompact",
            "PostCompact",
            "Notification",
        }
    ),
    compile_skill_scripts=True,
    reject_empty_always_on=True,
    stubs=(
        (
            "scripts/README.md",
            "<!-- c7: deterministic helpers, run as `python3 scripts/<name>.py`. "
            "Add one by emitting scripts/<name>.py. -->\n",
        ),
        (
            "sub_agents/README.md",
            "<!-- c5: delegated Claude Code subagents, each run in its OWN context. Add one as "
            "sub_agents/<name>/AGENT.md with frontmatter `description:`; the runner installs it "
            "as .claude/agents/<name>.md. -->\n",
        ),
        (
            ".claude/README.md",
            "<!-- c8: Claude Code hooks -- the only lever that acts INSIDE the tool loop, so the "
            'model cannot skip it. Add ONE file, .claude/settings.json: {"hooks": {"PostToolUse": '
            '[{"matcher": "Bash", "hooks": [{"type": "command", "command": "..."}]}]}}. Only the '
            '"hooks" key is installed. A PostToolUse command that prints '
            '{"decision":"block","reason":"..."} feeds the reason back to the model. -->\n',
        ),
        (
            ".claude/skills/README.md",
            "<!-- c6: reusable workflows. Add one as .claude/skills/<name>/SKILL.md with YAML "
            "frontmatter: `description:` is REQUIRED; `name:` is optional and defaults to the "
            "directory. Only name+description are advertised to the agent; the body is read on "
            "demand. -->\n",
        ),
    ),
)


CLAUDE_ROOT_ENTRIES = ("CLAUDE.md", ".claude", "scripts")
# HARNESS2_* carries the judge/codex/bridge keys; the agent never needs them.
_HOST_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_", "CLAUDECODE", "HARNESS2_")


class ClaudeSubstrate:
    """Claude Code behind the one Substrate interface."""

    name = "cc"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return CLAUDE_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage the claude_root entries; c5 moves to .claude/agents, c8 keeps only hooks."""
        if spec.install_mode != "claude_root":
            raise ValueError(
                f"install_mode {spec.install_mode!r} is not the claude mode; this substrate "
                f"has exactly one: 'claude_root'"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(CLAUDE_SURFACE, tree_dir):
            raise ValueError(f"{tree_dir} is not a claude harness tree (no CLAUDE.md)")
        staged = stage_entries(tree_dir, CLAUDE_ROOT_ENTRIES, "adapt-cc-root-")
        try:
            settings = staged / ".claude" / "settings.json"
            if settings.is_file():
                # Only its hooks are installed; permissions, env, model and auth keys are dropped.
                hooks = json.loads(settings.read_text(encoding="utf-8"))["hooks"]
                settings.write_text(json.dumps({"hooks": hooks}, indent=2), encoding="utf-8")
            for src in sorted(tree_dir.glob("sub_agents/*/AGENT.md")):
                name, body = src.parent.name, src.read_text(encoding="utf-8")
                fm = re.match(r"---\n(.*?)\n---\n", body, re.S)
                if fm and not re.search(r"^name:", fm.group(1), re.M):  # Claude Code needs it
                    body = f"---\nname: {name}\n{body[4:]}"
                dest = staged / ".claude" / "agents" / f"{name}.md"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(body, encoding="utf-8")
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        return Install(
            staging_dir=staged,
            fingerprint=RM.fingerprint(tree_dir),
            extras={
                "install_mode": "claude_root",
                **{k: v for k, v in ctx.items() if not k.startswith("file:")},
            },
        )

    def exec_argv(self, prompt: str, workspace: Path, model: str) -> list[str]:
        """The headless argv; run it with cwd=workspace so CLAUDE.md and .claude/ are found."""
        return [
            str(config.CLAUDE_BIN),
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model,
            "--permission-mode",
            "bypassPermissions",
            "--setting-sources",
            "project",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]

    def exec_env(self, base_env: Mapping[str, str], *, config_dir: Path) -> dict[str, str]:
        """Vertex AI routing in a throwaway config dir; the host's Claude settings never leak."""
        env = {k: v for k, v in base_env.items() if not k.startswith(_HOST_ENV_PREFIXES)}
        env.update(
            {
                "CLAUDE_CONFIG_DIR": str(config_dir),
                "CLAUDE_CODE_USE_VERTEX": "1",
                "ANTHROPIC_VERTEX_PROJECT_ID": config.VERTEX_PROJECT,
                "CLOUD_ML_REGION": config.VERTEX_REGION,
                "DISABLE_UPDATES": "1",
                "DISABLE_TELEMETRY": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            }
        )
        return env

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "ClaudeSubstrate was constructed without a launcher body; the benchmark "
                "adapter must supply one"
            )
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def run_failed(self, stream_path: Path) -> bool:
        """Whether the run's final `result` event reports an error."""
        results = [e for e in read_events(Path(stream_path)) if e.get("type") == "result"]
        return bool(results) and bool(results[-1].get("is_error"))

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000
    ) -> str:
        return render_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self) -> None:
        """Check the CLI, the model and the Vertex project; starts nothing."""
        config.require("HARNESS2_CLAUDE_MODEL", needed_by="the Claude Code executor")
        config.require("GOOGLE_CLOUD_PROJECT", needed_by="Claude Code on Vertex AI")
        if not config.CLAUDE_BIN.is_file():
            raise RuntimeError(
                "Claude Code is not installed; run harness2-setup <bench> --substrate cc"
            )
        if os.geteuid() == 0:
            raise RuntimeError(
                "Claude Code refuses bypassPermissions as root; run as a non-root user"
            )


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000) -> str:
    """Render Claude Code's `--output-format stream-json` lines into the shared grammar."""
    lines: list[str] = []
    for ev in read_events(Path(path)):
        et = ev.get("type")
        if et == "_read_error":
            return f"(failed to read trajectory: {ev.get('message')})"
        msg = ev.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if et == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    _emit_if(lines, "ASSISTANT", b.get("text") or "", obs_trunc)
                elif b.get("type") == "thinking":
                    _emit_if(lines, "REASONING", b.get("thinking") or "", obs_trunc)
                elif b.get("type") == "tool_use":
                    args = json.dumps(b.get("input"), ensure_ascii=False, default=str)
                    lines.append(f"[ACTION] {b.get('name')}({_clip(args, args_trunc)})")
        elif et == "user" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tag = "OBSERVATION ERROR" if b.get("is_error") else "OBSERVATION"
                    lines.append(f"[{tag}] {_clip(_text_of(b.get('content')), obs_trunc)}")
        elif et == "system" and ev.get("subtype") == "api_retry":
            lines.append(f"[HARNESS] api_retry attempt={ev.get('attempt')}: {ev.get('error')}")
        elif et == "result":
            usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
            lines.append(
                f"[HARNESS] result {ev.get('subtype')} | turns={ev.get('num_turns')} "
                f"tokens in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
                f"cost_usd={ev.get('total_cost_usd')}"
            )
            if ev.get("is_error"):
                errors = "; ".join(map(str, ev.get("errors") or [])) or str(ev.get("result"))
                lines.append(f"[ERROR] {_clip(errors, 1000)}")
    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered
