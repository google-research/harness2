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

"""ClaudeCodeSubstrate: Claude Code (`claude -p`) as a solver substrate.

Carries the c1..c8 surface (CLAUDE_CODE_SURFACE), the one install mode (claude_root:
CLAUDE.md, .claude/{skills,agents,settings.json} and scripts placed at the workspace root,
where `claude -p` run with that cwd discovers them), the `claude -p` flag contract, and the
stream-json trajectory renderer. Per-deployment differences are values in Install.extras,
never subclasses.

The editable tree differs from the installed one in two places, both applied here:
`sub_agents/<name>/AGENT.md` becomes `.claude/agents/<name>.md`, and `.claude/hooks.json`
is folded into a generated `.claude/settings.json`, so a proposal can add hooks but can
never touch permissions, env or the model.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest

CLAUDE_CODE_SURFACE = SurfaceSpec(
    name="claude_code",
    always_on=("CLAUDE.md",),
    always_on_max=8_000,
    always_on_cap_reason=(
        "CLAUDE.md is loaded whole into the context of EVERY session, and every token "
        "competes with the agent's own output budget"
    ),
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
    hooks_file=".claude/hooks.json",
    hook_types=frozenset({"command", "prompt", "agent"}),
    hook_events=frozenset(
        {
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
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
            "<!-- c5: Claude Code subagents, invoked with the Task tool in their OWN context. "
            "Add one as sub_agents/<name>/AGENT.md with frontmatter `description:`; the runner "
            "installs it as .claude/agents/<name>.md. -->\n",
        ),
        (
            ".claude/README.md",
            "<!-- c8: Claude Code lifecycle hooks -- the only lever that acts INSIDE the tool "
            "loop, so the model cannot skip it. Add ONE file, .claude/hooks.json: "
            '{"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", '
            '"command": "..."}]}]}}. A PostToolUse command that prints '
            '{"decision":"block","reason":"..."} feeds the reason back to the model. -->\n',
        ),
        (
            ".claude/skills/README.md",
            "<!-- c6: reusable workflows loaded on demand via the Skill tool. Add one as "
            ".claude/skills/<name>/SKILL.md with YAML frontmatter: `description:` is REQUIRED; "
            "`name:` is optional and must match the directory. Only name+description are "
            "advertised to the agent; the body is read on demand. -->\n",
        ),
    ),
)


CLAUDE_ROOT_ENTRIES = ("CLAUDE.md", ".claude", "scripts")

# Files a proposal may not smuggle into the installed .claude/ directory: settings are
# generated here, and the agents directory is generated from sub_agents/.
_GENERATED = (".claude/settings.json", ".claude/settings.local.json", ".claude/agents")

_NAME_LINE = re.compile(r"(?m)^name:\s*.*$")


def _agent_file(name: str, text: str) -> str:
    """AGENT.md -> a Claude Code agent definition whose `name:` is the directory name."""
    if text.lstrip().startswith("---"):
        head, sep, body = text.lstrip()[3:].partition("\n---")
        if sep:
            head = _NAME_LINE.sub("", head).strip("\n")
            if not re.search(r"(?m)^description:\s*\S", head):
                head += "\ndescription: delegated subagent"
            return f"---\nname: {name}\n{head}\n---{body}"
    return f"---\nname: {name}\ndescription: delegated subagent\n---\n\n{text}"


def settings_json(tree_dir: Path) -> dict:
    """The generated workspace .claude/settings.json: hooks from the tree, nothing else."""
    settings: dict = {}
    hooks = Path(tree_dir) / ".claude" / "hooks.json"
    if hooks.is_file():
        try:
            doc = json.loads(hooks.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f".claude/hooks.json is not valid JSON: {exc}") from exc
        if isinstance(doc, dict) and isinstance(doc.get("hooks"), dict):
            settings["hooks"] = doc["hooks"]
    return settings


def install_tree(tree_dir: Path, dest: Path) -> list[str]:
    """Copy a claude_code tree into `dest` in its runtime layout; return the installed paths."""
    tree_dir, dest = Path(tree_dir), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for entry in CLAUDE_ROOT_ENTRIES:
        src = tree_dir / entry
        if src.is_file():
            shutil.copy2(src, dest / entry)
            installed.append(entry)
        elif src.is_dir():
            for f in sorted(src.rglob("*")):
                rel = str(f.relative_to(tree_dir))
                if (
                    not f.is_file()
                    or "__pycache__" in f.parts
                    or rel == ".claude/hooks.json"
                    or any(rel == g or rel.startswith(g + "/") for g in _GENERATED)
                ):
                    continue
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out)
                installed.append(rel)
    for agent_md in sorted((tree_dir / "sub_agents").glob("*/AGENT.md")):
        name = agent_md.parent.name
        out = dest / ".claude" / "agents" / f"{name}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            _agent_file(name, agent_md.read_text(encoding="utf-8", errors="replace")),
            encoding="utf-8",
        )
        installed.append(str(out.relative_to(dest)))
    settings = settings_json(tree_dir)
    out = dest / ".claude" / "settings.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    installed.append(".claude/settings.json")
    return installed


class ClaudeCodeSubstrate:
    """Claude Code behind the one Substrate interface."""

    name = "cc"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return CLAUDE_CODE_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage the tree in its runtime layout, ready to be copied to the workspace root;
        there is no render step, because the CLI loads CLAUDE.md and .claude/ itself."""
        if spec.install_mode != "claude_root":
            raise ValueError(
                f"install_mode {spec.install_mode!r} is not the Claude Code mode; this "
                f"substrate has exactly one: 'claude_root'"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(CLAUDE_CODE_SURFACE, tree_dir):
            raise ValueError(f"{tree_dir} is not a Claude Code harness tree (no CLAUDE.md)")
        staged = Path(tempfile.mkdtemp(prefix="adapt-cc-root-"))
        try:
            install_tree(tree_dir, staged)
            for key, body in ctx.items():
                if key.startswith("file:"):
                    p = staged / HT.normalize_rel(key[len("file:") :])
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(body, encoding="utf-8")
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

    def exec_argv(
        self,
        prompt: str,
        workspace: Path,
        model: str,
        *,
        effort: str = "",
        max_budget_usd: str = "",
        extra_dirs: tuple[str, ...] = (),
    ) -> list[str]:
        """The `claude -p` argv every launcher uses; run it with cwd=workspace.

        Only project settings load, so the operator's own CLAUDE.md, hooks, plugins and MCP
        servers never leak into a rollout, and the harness under test is the whole harness.
        Callers use stdin=DEVNULL and `exec_env`.
        """
        argv = [
            config.CLAUDE_BIN,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-hook-events",
            "--dangerously-skip-permissions",
            "--setting-sources",
            config.CLAUDE_SETTING_SOURCES,
            "--strict-mcp-config",
            "--no-session-persistence",
            "--add-dir",
            str(workspace),
        ]
        for d in extra_dirs:
            argv += ["--add-dir", str(d)]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
        if max_budget_usd:
            argv += ["--max-budget-usd", str(max_budget_usd)]
        return argv

    def exec_env(self, base_env: Mapping[str, str]) -> dict[str, str]:
        """The `claude -p` environment: provider selection comes from the operator's env
        (ANTHROPIC_API_KEY, a logged-in CLI, or CLAUDE_CODE_USE_VERTEX / _BEDROCK)."""
        env = dict(base_env)
        for k in config.CLAUDE_NESTED_ENV:
            env.pop(k, None)
        env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
        env.setdefault("DISABLE_AUTOUPDATER", "1")
        if config.CLAUDE_MAX_OUTPUT_TOKENS:
            env.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", str(config.CLAUDE_MAX_OUTPUT_TOKENS))
        return env

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "ClaudeCodeSubstrate was constructed without a launcher body; the benchmark "
                "adapter must supply one (it owns workspace layout and read-back locations)"
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

    def preflight(self) -> None:
        """Checks the CLI exists and runs; starts no model session."""
        binary = shutil.which(config.CLAUDE_BIN) or (
            config.CLAUDE_BIN if Path(config.CLAUDE_BIN).is_file() else None
        )
        if not binary:
            raise RuntimeError(
                f"Claude Code CLI not found ({config.CLAUDE_BIN!r}): install it "
                f"(https://code.claude.com/docs) or set HARNESS2_CLAUDE_BIN"
            )
        try:
            r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"`{binary} --version` failed: {exc}") from exc
        if r.returncode != 0:
            raise RuntimeError(f"`{binary} --version` exited {r.returncode}: {r.stderr[-300:]}")


def run_in_group(cmd: list[str], *, cwd: Path, env: dict, stdout, stderr, timeout: int) -> int:
    """Run `cmd` in its own process group with stdin closed; on timeout, TERM then KILL the
    whole group and return 124 like timeout(1), so a hung CLI never outlives its budget."""
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=30)
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()
        return 124


_TRUNC_FMT = "…(+{n} chars — render truncation)"


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\x00", "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:n] + _TRUNC_FMT.format(n=len(s) - n)


def _result_text(content) -> str:
    """tool_result content: a string, or a list of text/image blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    out.append(str(block.get("text") or ""))
                elif block.get("type") == "image":
                    out.append("(image)")
            elif isinstance(block, str):
                out.append(block)
        return "\n".join(out)
    return "" if content is None else json.dumps(content, default=str, ensure_ascii=False)


def _tool_args(name: str, tin) -> str:
    """Bash shows its command, file tools their path; everything else compact JSON."""
    if isinstance(tin, dict):
        if name == "Bash" and isinstance(tin.get("command"), str):
            return tin["command"]
        if set(tin) <= {"file_path"} and isinstance(tin.get("file_path"), str):
            return tin["file_path"]
    return json.dumps(tin, default=str, ensure_ascii=False) if tin is not None else ""


def parse_events(path: Path) -> list[dict]:
    """The stream-json events of one session, junk lines tolerated."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    events = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and isinstance(ev.get("type"), str):
            events.append(ev)
    return events


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000) -> str:
    """Render a `claude -p --output-format stream-json --verbose` stream into the shared
    grammar. Subagent turns (parent_tool_use_id set) are tagged so they read as delegated."""
    try:
        events = parse_events(Path(path))
    except OSError as e:
        return f"(failed to read trajectory: {e})"
    lines: list[str] = []
    for ev in events:
        et = ev.get("type")
        sub = "[SUBAGENT] " if ev.get("parent_tool_use_id") else ""
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        content = msg.get("content")
        if et == "system":
            st = ev.get("subtype", "")
            if st == "init":
                lines.append(
                    f"[HARNESS] session init | model={ev.get('model')} | "
                    f"skills={len(ev.get('skills') or [])} agents={len(ev.get('agents') or [])}"
                )
            elif st == "compact_boundary":
                lines.append("[HARNESS] context compacted")
            elif st == "hook_response":
                # c8 evidence: which hook fired, how it exited, and what it fed back.
                out = ev.get("output") or ev.get("stdout") or ev.get("stderr") or ""
                lines.append(
                    f"[HARNESS] hook {ev.get('hook_name') or ev.get('hook_event') or ''} "
                    f"exit={ev.get('exit_code')}"
                    + (f": {_clip(out, obs_trunc)}" if str(out).strip() else "")
                )
            continue
        if et == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text" and str(block.get("text") or "").strip():
                    lines.append(f"{sub}[ASSISTANT] {_clip(block['text'], obs_trunc)}")
                elif bt == "thinking" and str(block.get("thinking") or "").strip():
                    lines.append(f"{sub}[THINKING] {_clip(block['thinking'], obs_trunc)}")
                elif bt == "tool_use":
                    name = block.get("name") or "tool"
                    args = _clip(_tool_args(name, block.get("input")), args_trunc)
                    lines.append(f"{sub}[ACTION] {name}({args})")
            continue
        if et == "user":
            if isinstance(content, str) and content.strip():
                lines.append(f"{sub}[USER] {_clip(content, obs_trunc)}")
                continue
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tag = "OBSERVATION ERROR" if block.get("is_error") else "OBSERVATION"
                    lines.append(
                        f"{sub}[{tag}] {_clip(_result_text(block.get('content')), obs_trunc)}"
                    )
                elif block.get("type") == "text" and str(block.get("text") or "").strip():
                    lines.append(f"{sub}[USER] {_clip(block['text'], obs_trunc)}")
            continue
        if et == "result":
            u = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
            lines.append(
                "[HARNESS] session {st} | turns={t} cost_usd={c} | tokens in={i} "
                "cache_read={cr} cache_write={cw} out={o}".format(
                    st=ev.get("subtype"),
                    t=ev.get("num_turns"),
                    c=ev.get("total_cost_usd"),
                    i=u.get("input_tokens"),
                    cr=u.get("cache_read_input_tokens"),
                    cw=u.get("cache_creation_input_tokens"),
                    o=u.get("output_tokens"),
                )
            )
            if ev.get("is_error"):
                lines.append(f"[ERROR] {_clip(ev.get('result') or ev.get('subtype'), 1000)}")
    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered


def summarize(path: Path) -> dict:
    """Counts and usage of one stream, for metrics files; zero events means a dead chain."""
    try:
        events = parse_events(Path(path))
    except OSError:
        events = []
    tools: list[str] = []
    final_text = ""
    result: dict = {}
    for ev in events:
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        if ev.get("type") == "assistant" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tools.append(_tool_args(block.get("name") or "", block.get("input")))
                elif (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and not ev.get("parent_tool_use_id")
                    and str(block.get("text") or "").strip()
                ):
                    final_text = block["text"]
        elif ev.get("type") == "result":
            result = ev
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return {
        "n_events": len(events),
        "tool_calls": len(tools),
        "commands": tools,
        "turns": int(result.get("num_turns") or 0),
        "subtype": result.get("subtype"),
        "is_error": bool(result.get("is_error")),
        "cost_usd": float(result.get("total_cost_usd") or 0.0),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "final_text": str(result.get("result") or final_text),
    }


def final_message(path: Path) -> str:
    """The session's final reply: the result event's text, else the last top-level text."""
    return summarize(Path(path))["final_text"]


__all__ = [
    "CLAUDE_CODE_SURFACE",
    "ClaudeCodeSubstrate",
    "install_tree",
    "run_in_group",
    "render_events",
    "summarize",
    "final_message",
]
