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

"""CodexSubstrate: codex-cli (config.CODEX_VERSION) as a solver substrate.

Carries the c1..c8 surface (CODEX_SURFACE), the one install mode (codex_root: AGENTS.md,
.agents, scripts and .codex copied to the workspace root, where `codex exec -C <ws>` finds
them), the `codex exec` flag contract, and the codex thread-item trajectory renderer.
Per-deployment differences are values in Install.extras, never subclasses.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
from pathlib import Path
from typing import Mapping

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest

CODEX_SURFACE = SurfaceSpec(
    name="codex",
    always_on=("AGENTS.md",),
    always_on_max=8_000,
    always_on_cap_reason=(
        "AGENTS.md is injected whole into EVERY turn, and codex additionally "
        "truncates it silently at 32,768 bytes"
    ),
    skill_dir=".agents/skills",
    skill_name_rule="overrides_dir",
    trigger_hint=(
        'Fix it by adding a trigger line naming `{name}` to AGENTS.md, e.g. "When '
        "<the situation this skill handles>, read .agents/skills/{name}/SKILL.md "
        'and follow it."'
    ),
    autotrigger_file="AGENTS.md",
    trigger_line="- When {desc}, read .agents/skills/{name}/SKILL.md and follow it.\n",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file=".codex/hooks.json",
    hook_events=frozenset(
        {
            "PreToolUse",
            "PostToolUse",
            "PermissionRequest",
            "PreCompact",
            "PostCompact",
            "SessionStart",
            "SubagentStart",
            "SubagentStop",
            "UserPromptSubmit",
            "Stop",
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
            "<!-- c5: delegated codex agent roles, each spawned in its OWN context. Add one as "
            "sub_agents/<name>/AGENT.md with frontmatter `description:`; the runner installs it "
            "as a [agents.<name>] role. -->\n",
        ),
        (
            ".codex/README.md",
            "<!-- c8: codex lifecycle hooks -- the only lever that acts INSIDE the tool loop, "
            "so the model cannot skip it. Add ONE file, .codex/hooks.json, Claude-Code hook "
            'schema: {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": '
            '"command", "command": "..."}]}]}}. A PostToolUse command that prints '
            '{"decision":"block","reason":"..."} feeds the reason back to the model. -->\n',
        ),
        (
            ".agents/skills/README.md",
            "<!-- c6: reusable workflows. Add one as .agents/skills/<name>/SKILL.md with YAML "
            "frontmatter: `description:` is REQUIRED (no frontmatter = skill silently dropped); "
            "`name:` is optional and must match the directory. Only name+description are "
            "advertised to the agent; the body is read on demand. -->\n",
        ),
    ),
)


CODEX_ROOT_ENTRIES = ("AGENTS.md", ".agents", "scripts", ".codex")


class CodexSubstrate:
    """codex-cli behind the one Substrate interface."""

    name = "cx"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return CODEX_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage exactly the codex_root entries for a copy to the workspace root; there is
        no render step, because the CLI injects AGENTS.md itself."""
        if spec.install_mode != "codex_root":
            raise ValueError(
                f"install_mode {spec.install_mode!r} is not the codex mode; this substrate "
                f"has exactly one: 'codex_root'"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(CODEX_SURFACE, tree_dir):
            raise ValueError(f"{tree_dir} is not a codex harness tree (no AGENTS.md)")
        staged = Path(tempfile.mkdtemp(prefix="adapt-cx-root-"))
        for entry in CODEX_ROOT_ENTRIES:
            src = tree_dir / entry
            if not src.exists():
                continue
            if src.is_dir():
                shutil.copytree(src, staged / entry)
            else:
                shutil.copy2(src, staged / entry)
        return Install(
            staging_dir=staged,
            fingerprint=RM.fingerprint(tree_dir),
            extras={
                "install_mode": "codex_root",
                **{k: v for k, v in ctx.items() if not k.startswith("file:")},
            },
        )

    def exec_argv(
        self, prompt: str, workspace: Path, model: str, *, reasoning_effort: str = "high"
    ) -> list[str]:
        """The reference `codex exec` argv; the real launchers build their own.

        Hook trust enables the editable hooks; JSON preserves ephemeral sessions.
        Callers use stdin=DEVNULL and `exec_env`.
        """
        return [
            str(Path(config.CODEX_DIST) / "codex"),
            "exec",
            prompt,
            "--model",
            model,
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "--ephemeral",
            "--json",
            "-C",
            str(workspace),
            "--add-dir",
            str(workspace),
        ]

    def exec_env(self, base_env: Mapping[str, str], *, codex_home: Path) -> dict[str, str]:
        """The reference `codex exec` environment; the real launchers build their own.

        The custom provider comes from CODEX_HOME, not a global OpenAI URL override.
        """
        env = dict(base_env)
        env.pop("OPENAI_BASE_URL", None)
        env["CODEX_HOME"] = str(codex_home)
        env["PATH"] = str(Path(config.CODEX_DIST) / "codex-path") + os.pathsep + env.get("PATH", "")
        return env

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "CodexSubstrate was constructed without a launcher body; the benchmark "
                "adapter must supply one (it owns runner choice, CODEX_HOME flavour and "
                "read-back locations)"
            )
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000
    ) -> str:
        return render_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self, *, require_dist: bool = False, require_bridge: bool = False) -> None:
        """Check the CLI and Responses endpoint, including container configuration."""
        from urllib.parse import urlparse

        config.require("HARNESS2_CODEX_BASE_URL", needed_by="Codex Responses endpoint")
        config.require("HARNESS2_CODEX_API_KEY", needed_by="Codex endpoint authentication")
        url = urlparse(config.CODEX_BASE_URL)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise config.ConfigError("HARNESS2_CODEX_BASE_URL must be an HTTP(S) URL")
        binary = Path(config.CODEX_DIST) / "codex"
        if not binary.is_file():
            raise RuntimeError(
                "Codex is not installed; run scripts/setup_benchmark.py with --substrate cx"
            )
        if require_bridge and not config.CODEX_CONTAINER_URL:
            raise RuntimeError(
                "Set HARNESS2_CODEX_CONTAINER_URL to a container-accessible Responses endpoint"
            )
        with socket.create_connection(
            (url.hostname, url.port or (443 if url.scheme == "https" else 80)), timeout=5
        ):
            pass


_TRUNC_FMT = "…(+{n} chars — render truncation)"
_AGENTS_MD_TITLE = "# AGENTS.md instructions"


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\x00", "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:n] + _TRUNC_FMT.format(n=len(s) - n)


def _text_of(obj) -> str:
    """Best-effort text extraction from a message-ish payload (str, .text, content list)."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for k in ("text", "message", "content", "summary"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, (list, dict)):
                t = _text_of(v)
                if t.strip():
                    return t
        return ""
    if isinstance(obj, list):
        return "\n".join(t for t in (_text_of(x) for x in obj) if t.strip())
    return ""


def parse_items(path: Path) -> list[dict]:
    """Read the exec --json stream (junk tolerated), in stream order, thread-items deduped
    by .item.id: last state wins, first-appearance position kept."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [{"type": "_read_error", "message": str(e)}]
    raw = raw.replace("\x00", "")

    events: list[dict] = []
    by_id: dict[str, int] = {}
    for ln in raw.splitlines():
        ln = ln.strip()

        if not (ln.startswith('{"type":') or ln.startswith('{"timestamp":')):
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        item = ev.get("item")
        iid = item.get("id") if isinstance(item, dict) else None
        if iid is not None:
            if iid in by_id:
                events[by_id[iid]] = ev
                continue
            by_id[iid] = len(events)
        events.append(ev)
    return events


def _render_item(item: dict, lines: list[str], obs_trunc: int, args_trunc: int) -> None:
    itype = item.get("type", "")

    if itype == "command_execution":
        cmd = _clip(item.get("command"), args_trunc)
        lines.append(f"[ACTION] shell: {cmd}")
        status = item.get("status")
        exit_code = item.get("exit_code")
        failed = status == "failed" or (exit_code not in (None, 0))
        obs = _clip(item.get("aggregated_output"), obs_trunc)
        suffix = "" if exit_code in (None, 0) else f" | exit_code={exit_code}"
        if status == "in_progress" and not obs:
            obs = "(no output captured — command still in_progress at end of stream)"
        lines.append(f"[OBSERVATION{' ERROR' if failed else ''}] {obs}{suffix}")
        return

    if itype == "agent_message":
        txt = item.get("text")
        if isinstance(txt, str) and txt.strip():
            lines.append(f"[ASSISTANT] {_clip(txt, obs_trunc)}")
        return

    if itype == "reasoning":
        txt = _text_of(item)
        if txt.strip():
            lines.append(f"[REASONING] {_clip(txt, obs_trunc)}")
        return

    if itype == "todo_list":
        rows = item.get("items") or []
        if isinstance(rows, list) and rows:
            plan = " | ".join(
                f"[{'x' if r.get('completed') else ' '}] {_clip(r.get('text'), 200)}"
                for r in rows
                if isinstance(r, dict)
            )
            lines.append(f"[ASSISTANT] plan: {plan}")
        return

    if itype == "error":
        lines.append(f"[HARNESS] {_clip(item.get('message'), 1000)}")
        return

    if itype in ("user_message", "message"):
        txt = _text_of(item)
        if txt.lstrip().startswith(_AGENTS_MD_TITLE):
            lines.append(f"[HARNESS AGENTS.md] {_clip(txt, obs_trunc)}")
        elif txt.strip():
            lines.append(f"[USER] {_clip(txt, obs_trunc)}")
        return

    txt = _text_of(item)
    if txt.strip():
        lines.append(f"[HARNESS] {itype or 'item'}: {_clip(txt, obs_trunc)}")


def _emit(lines: list[str], line: str) -> None:

    if not lines or lines[-1] != line:
        lines.append(line)


def _emit_if(lines: list[str], tag: str, txt: str, trunc: int) -> None:
    if txt.strip():
        _emit(lines, f"[{tag}] {_clip(txt, trunc)}")


def _emit_user(lines: list[str], txt: str, obs_trunc: int) -> None:
    """A user-role payload, minus the trap: AGENTS.md arrives as one but is harness text."""
    if txt.lstrip().startswith(_AGENTS_MD_TITLE):
        _emit(lines, f"[HARNESS AGENTS.md] {_clip(txt, obs_trunc)}")
    else:
        _emit_if(lines, "USER", txt, obs_trunc)


def _render_rollout_payload(
    payload: dict, lines: list[str], obs_trunc: int, args_trunc: int
) -> None:
    """~/.codex/sessions rollout lines (response_item / event_msg payloads)."""
    ptype = payload.get("type", "")
    if ptype == "function_call":
        args = payload.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass
        cmd = args.get("cmd") if isinstance(args, dict) else args
        name = payload.get("name") or "shell"
        _emit(lines, f"[ACTION] {name}: {_clip(cmd if cmd is not None else args, args_trunc)}")
    elif ptype == "function_call_output":
        out = payload.get("output")
        _emit(lines, f"[OBSERVATION] {_clip(_text_of(out) or out, obs_trunc)}")
    elif ptype == "message":
        role = payload.get("role", "")
        txt = _text_of(payload)
        if role == "user":
            _emit_user(lines, txt, obs_trunc)
        elif role == "assistant":
            _emit_if(lines, "ASSISTANT", txt, obs_trunc)
        else:
            _emit_if(lines, f"HARNESS {role or 'message'}", txt, obs_trunc)
    elif ptype == "user_message":
        _emit_user(lines, _text_of(payload), obs_trunc)
    elif ptype == "agent_message":
        _emit_if(lines, "ASSISTANT", _text_of(payload), obs_trunc)
    elif ptype == "reasoning":
        _emit_if(lines, "REASONING", _text_of(payload), obs_trunc)


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000) -> str:
    """Render a codex exec/rollout stream into the shared grammar.

    `obs_trunc` and `max_chars` come from the cell's BenchSpec. Tool input is clipped at
    `args_trunc`, a codex-substrate default that BenchSpec does not carry and no caller
    overrides -- a signature default rather than a module constant, so a future caller can
    thread it without touching this function.
    """
    lines: list[str] = []
    for ev in parse_items(Path(path)):
        et = ev.get("type", "")

        if et == "_read_error":
            return f"(failed to read trajectory: {ev.get('message')})"

        item = ev.get("item")
        if isinstance(item, dict):
            _render_item(item, lines, obs_trunc, args_trunc)
            continue

        payload = ev.get("payload")
        if isinstance(payload, dict):
            _render_rollout_payload(payload, lines, obs_trunc, args_trunc)
            continue

        if et == "turn.completed":
            usage = ev.get("usage")
            if isinstance(usage, dict):
                lines.append(
                    "[HARNESS] turn completed | tokens in={i} cached={c} out={o} "
                    "reasoning={r}".format(
                        i=usage.get("input_tokens"),
                        c=usage.get("cached_input_tokens"),
                        o=usage.get("output_tokens"),
                        r=usage.get("reasoning_output_tokens"),
                    )
                )
            continue

        if et in ("turn.failed", "error") or isinstance(ev.get("error"), (dict, str)):
            msg = _text_of(ev.get("error")) or _text_of(ev) or json.dumps(ev)[:300]
            lines.append(f"[ERROR] {_clip(msg, 1000)}")
            continue

    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered


def final_message(path: Path) -> str:
    """Last non-empty agent_message text: the deliverable summary before turn.completed."""
    last = ""
    for ev in parse_items(Path(path)):
        item = ev.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            txt = item.get("text")
            if isinstance(txt, str) and txt.strip():
                last = txt
            continue
        payload = ev.get("payload")
        if isinstance(payload, dict):
            if payload.get("type") == "agent_message":
                txt = _text_of(payload)
                if txt.strip():
                    last = txt
            elif payload.get("type") == "task_complete":
                txt = _text_of(payload.get("last_agent_message"))
                if txt.strip():
                    last = txt
    return last
