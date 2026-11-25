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

"""PiSubstrate: the pi coding agent (pi.dev) as a solver substrate.

Carries the c1..c8 surface (PI_SURFACE) and the single install mode `pi_project`,
which stages a workspace overlay pi loads natively:

    .pi/APPEND_SYSTEM.md   c1-c4 composed (system prompt, guardrails, memory,
                           tool guidance) — appended to pi's system prompt
    .pi/skills/<n>/SKILL.md    c6 skills (pi implements the Agent Skills spec)
    .pi/extensions/<n>.ts      c8 plugins (pi extensions: tools, commands,
                               event handlers inside the agent process)
    .pi/prompts/<n>.md         c5 subagents (pi prompt templates; a launcher
                               runs each as a fresh `pi --print` subprocess)
    scripts/<n>.py             c7 deterministic helpers (unchanged contract)

pi loads project-local `.pi` resources only after project trust; the reference
argv passes `-a` because the overlay is staged by this harness, not by a human.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest

PI_SURFACE = SurfaceSpec(
    name="pi",
    always_on=("systemprompt.md", "guardrails.md", "memory.md", "tool_guidance.md"),
    always_on_max=16_000,
    always_on_cap_reason=(
        "APPEND_SYSTEM.md is injected whole into the system prompt of every "
        "request; every token here competes with the agent's own context budget"
    ),
    skill_dir="skills",
    skill_name_rule="must_match",
    trigger_hint=(
        "Fix it by adding a trigger line naming `{name}` to systemprompt.md or "
        "guardrails.md, e.g. \"When <the situation this skill handles>, load the "
        "`{name}` skill.\""
    ),
    autotrigger_file="guardrails.md",
    trigger_line="- When {desc}, load the `{name}` skill.\n",
    subagent_requires_mode=True,
    plugin_dir="plugin",
    hooks_file=None,
    stubs=(
        (
            "scripts/README.md",
            "<!-- c7: deterministic helpers, run as `python3 scripts/<name>.py`. "
            "Add one by emitting scripts/<name>.py. -->\n",
        ),
        (
            "sub_agents/README.md",
            "<!-- c5: delegated subagents. Materialised as pi prompt templates "
            "(.pi/prompts/<name>.md); the model invokes the template and the "
            "launcher runs it as a fresh `pi --print` subprocess in its own "
            "context. Add one as sub_agents/<name>/AGENT.md with frontmatter "
            "`description:`. -->\n",
        ),
        (
            "plugin/README.md",
            "<!-- c8: pi extensions (.pi/extensions/<name>.ts) — TypeScript that "
            "registers tools, commands, and event handlers INSIDE the pi process; "
            "the only lever that can enforce something the model cannot skip. "
            "Add one as plugin/<name>.ts exporting `export default function (pi)`. -->\n",
        ),
        (
            "skills/README.md",
            "<!-- c6: pi skills (Agent Skills spec, agentskills.io): "
            "skills/<name>/SKILL.md with frontmatter `name:` (must match the "
            "directory) and `description:`. Loaded on demand; advertised by "
            "description at startup. -->\n",
        ),
    ),
)

_PI_INSTALL_MODES = ("pi_project",)

_FRONTMATTER_DESC = re.compile(r"^description:\s*(.+)$", re.M)


def _desc(body: str) -> str:
    m = _FRONTMATTER_DESC.search(body[:600])
    return m.group(1).strip() if m else "(no description)"


def compose_append_system(staged: Path) -> str:
    """Build `.pi/APPEND_SYSTEM.md` from the staged always-on files.

    Mirrors opencode.compose_prompt, minus the Jinja placeholder: pi receives the
    task instruction through its own argv, so the composed text needs no slot.
    """
    parts = [
        t
        for f in PI_SURFACE.always_on
        if (staged / f).is_file() and (t := (staged / f).read_text(errors="replace").strip())
    ]
    skills = sorted(staged.glob("skills/*/SKILL.md"), key=lambda p: p.parent.name)
    if skills:
        idx = "\n".join(f"- `{p.parent.name}`: {_desc(p.read_text(errors='replace'))}" for p in skills)
        parts.append(
            "# AVAILABLE SKILLS\nLoad the matching skill (read its SKILL.md) when the "
            f"situation matches:\n{idx}"
        )
    subs = sorted(staged.glob("sub_agents/*/AGENT.md"), key=lambda p: p.parent.name)
    if subs:
        idx = "\n".join(f"- `{p.parent.name}`: {_desc(p.read_text(errors='replace'))}" for p in subs)
        parts.append(
            "# AVAILABLE SUBAGENTS\nDelegate isolated work to a subagent by invoking its "
            "prompt template (`/` command) in a fresh `pi --print` subprocess when the "
            f"situation matches:\n{idx}"
        )
    scripts = sorted(p.name for p in staged.glob("scripts/*.py"))
    if scripts:
        parts.append(
            f"# HELPER SCRIPTS\nAvailable at `scripts/` in the workspace root: "
            f"{', '.join(scripts)}\nRun with `python3 scripts/<name>.py`."
        )
    return "\n\n---\n\n".join(parts)


def pi_bin() -> Path:
    return Path(os.environ.get("PI_BIN", str(Path.home() / ".pi" / "agent" / "bin" / "pi")))


_TRUNC_FMT = "…(+{n} chars — render truncation)"


def _clip(value: object, n: int) -> str:
    text = "" if value is None else str(value)
    if n <= 0 or len(text) <= n:
        return text
    return text[:n] + _TRUNC_FMT.format(n=len(text) - n)


def _jsonish(value: object) -> str:
    if value is None or isinstance(value, str):
        return str(value or "")
    return json.dumps(value, default=str, ensure_ascii=False)


def _tool_result(value: object) -> str:
    """Unwrap the common pi result envelope while preserving complex payloads."""
    if not isinstance(value, dict):
        return _jsonish(value)
    content = value.get("content", value)
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )
    return _jsonish(content)


def render_pi_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400) -> str:
    """Render pi's JSON event stream into the shared trajectory grammar.

    The stream is JSONL: tool_execution_start/end, message_update, and the final
    assistant message_end. Tool start/end records are merged so one call becomes
    one ACTION/OBSERVATION pair.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(failed to read trajectory: {e})"

    lines: list[str] = []
    starts: dict[str, tuple[str, object]] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("error"), dict):
            error = event["error"]
            msg = error.get("message") or _jsonish(error)
            lines.append(f"[ERROR] {_clip(msg, obs_trunc)}")
            continue

        etype = event.get("type")
        call_id = str(event.get("toolCallId") or event.get("id") or "")
        if etype == "tool_execution_start":
            name = str(event.get("toolName") or "tool")
            if call_id:
                starts[call_id] = (name, event.get("args"))
            else:
                lines.append(f"[ACTION] {name}({_clip(_jsonish(event.get('args')), args_trunc)})")
        elif etype == "tool_execution_end":
            name, args = starts.pop(call_id, (str(event.get("toolName") or "tool"), None))
            if call_id and call_id in starts:
                name, args = starts[call_id]
                starts.pop(call_id, None)
            lines.append(f"[ACTION] {name}({_clip(_jsonish(args), args_trunc)})")
            if event.get("isError"):
                lines.append(f"[OBSERVATION ERROR] {_clip(_tool_result(event.get('result')), obs_trunc)}")
            else:
                lines.append(f"[OBSERVATION] {_clip(_tool_result(event.get('result')), obs_trunc)}")
        elif etype == "message_end" and isinstance(event.get("message"), dict):
            message = event["message"]
            if message.get("role") == "assistant" and isinstance(message.get("content"), list):
                text = "".join(
                    block.get("text", "")
                    for block in message["content"]
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
                )
                if text.strip():
                    lines.append(f"[ASSISTANT] {_clip(text, obs_trunc)}")

    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered


class PiSubstrate:
    """pi behind the one Substrate interface; the launcher body is benchmark-supplied."""

    name = "pi"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return PI_SURFACE

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError("PiSubstrate.execute requires a benchmark-supplied launcher")
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        mode = spec.install_mode
        if mode not in _PI_INSTALL_MODES:
            raise ValueError(
                f"install_mode {mode!r} is not a pi mode; this substrate supports "
                f"{_PI_INSTALL_MODES}"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(PI_SURFACE, tree_dir):
            raise ValueError(
                f"{tree_dir} is not a pi harness tree (no always-on file present)"
            )
        staged = Path(tempfile.mkdtemp(prefix="adapt-pi-project-"))
        try:
            # 1. composed always-on text -> .pi/APPEND_SYSTEM.md
            composed = compose_append_system(tree_dir)
            (staged / ".pi").mkdir(parents=True)
            (staged / ".pi" / "APPEND_SYSTEM.md").write_text(composed, encoding="utf-8")

            # 2. c6 skills -> .pi/skills/<name>/SKILL.md (native, same format)
            for skill in tree_dir.glob("skills/*/SKILL.md"):
                dest = staged / ".pi" / "skills" / skill.parent.name
                dest.mkdir(parents=True)
                skill_scripts = skill.parent / "scripts"
                if skill_scripts.is_dir():
                    shutil.copytree(skill_scripts, dest / "scripts", dirs_exist_ok=True)
                shutil.copy2(skill, dest / "SKILL.md")

            # 3. c8 plugins -> .pi/extensions/<name>.ts (native pi extensions)
            for plugin in tree_dir.glob("plugin/*.ts"):
                dest = staged / ".pi" / "extensions"
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(plugin, dest / plugin.name)

            # 4. c5 subagents -> .pi/prompts/<name>.md (prompt templates)
            for agent in tree_dir.glob("sub_agents/*/AGENT.md"):
                dest = staged / ".pi" / "prompts"
                dest.mkdir(parents=True, exist_ok=True)
                body = agent.read_text(encoding="utf-8")
                (dest / f"{agent.parent.name}.md").write_text(body, encoding="utf-8")

            # 5. c7 scripts -> workspace scripts/ (unchanged contract)
            if (tree_dir / "scripts").is_dir():
                shutil.copytree(tree_dir / "scripts", staged / "scripts", dirs_exist_ok=True)

            # 6. benchmark-owned integration files ride along at their rel paths
            for key, body in ctx.items():
                if key.startswith("file:"):
                    rel = HT.normalize_rel(key[len("file:") :])
                    p = staged / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(body, encoding="utf-8")
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise

        return Install(
            staging_dir=staged, fingerprint=RM.fingerprint(tree_dir), extras={"install_mode": mode}
        )

    def run_argv(
        self,
        prompt: str,
        workspace: Path,
        model: str,
        *,
        title: str = "adapt",
        variant: str | None = None,
        agent: str = "",
        session: str | None = None,
    ) -> list[str]:
        """The reference argv; the real launchers build their own.

        Run with cwd=workspace so pi loads the staged `.pi` overlay. `-a` trusts the
        project-local overlay for this process (we staged it ourselves); `--mode json`
        emits the pi event stream for trajectory rendering.
        """
        argv = [
            str(pi_bin()),
            "--print",
            "--mode",
            "json",
            "-a",
            "--model",
            model,
            "--name",
            title,
            "--session-dir",
            str(Path(workspace) / ".pi-sessions"),
            "--",
            prompt,
        ]
        return argv

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400
    ) -> str:
        return render_pi_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self) -> None:
        """Check that the pi CLI is present and runnable; starts no model call."""
        binary = pi_bin()
        if not binary.is_file():
            raise RuntimeError(
                f"pi CLI missing at {binary}: install pi.dev or set PI_BIN to its executable"
            )
        try:
            proc = subprocess.run(
                [str(binary), "--version"], capture_output=True, text=True, timeout=10, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"pi CLI at {binary} is not runnable: {e}") from e
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "no output").strip()
            raise RuntimeError(f"pi --version failed ({proc.returncode}): {detail}")
