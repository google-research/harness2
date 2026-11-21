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

"""HermesSubstrate: Hermes Agent (`hermes` CLI) as a solver substrate.

Carries the c1..c7 surface (`HERMES_SURFACE`), the single install mode `hermes_home`
(`SOUL.md` and `skills/` staged into an isolated `HERMES_HOME`, with `scripts/` and
`sub_agents/` staged as a workspace overlay), the `hermes chat -q ... --format stream-json`
flag contract, and the `stream-json` JSONL trajectory renderer.
"""

from __future__ import annotations

import json
import os
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

HERMES_SURFACE = SurfaceSpec(
    name="hermes",
    always_on=("SOUL.md",),
    always_on_max=24_000,
    always_on_cap_reason=(
        "SOUL.md is injected whole into EVERY Hermes Agent session; every token here "
        "competes with the agent's own context and output budget"
    ),
    skill_dir="skills",
    skill_name_rule="must_match",
    trigger_hint=(
        'Fix it by adding a trigger line naming `{name}` to SOUL.md, e.g. "When '
        '<the situation this skill handles>, load the `{name}` skill."'
    ),
    autotrigger_file="SOUL.md",
    trigger_line="- When {desc}, load the `{name}` skill.\n",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file=None,
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
            "<!-- c5: delegated subagent role briefs, spawned in their OWN context via "
            "`delegate_task`. Add one as sub_agents/<name>/AGENT.md with frontmatter "
            "`description:`. -->\n",
        ),
        (
            "skills/README.md",
            "<!-- c6: reusable workflows loaded on demand via `skill_view(name)`. "
            "Add one as skills/<name>/SKILL.md with YAML frontmatter `name:` matching "
            "the directory and a `description:`. -->\n",
        ),
    ),
)

HERMES_HOME_ENTRIES = ("SOUL.md", "skills")
HERMES_WORKSPACE_ENTRIES = ("scripts", "sub_agents")


def resolve_hermes_bin() -> str | None:
    """Configured `HARNESS2_HERMES_BIN`, else `hermes` on PATH."""
    configured = config.env("HARNESS2_HERMES_BIN")
    if configured:
        p = Path(configured).expanduser()
        return str(p) if p.is_file() else shutil.which(configured)
    return shutil.which("hermes")


def stage_workspace(install: Install, workspace: Path) -> None:
    """Copy staged workspace overlay entries (`scripts/`, `sub_agents/`, `file:*`) into `workspace`."""
    overlay = Path(install.extras.get("workspace_overlay", install.staging_dir / "workspace"))
    if not overlay.is_dir():
        return
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    for item in sorted(overlay.iterdir()):
        dst = workspace / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)


class HermesSubstrate:
    """Hermes Agent CLI behind the shared `Substrate` interface."""

    name = "hm"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return HERMES_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage `SOUL.md` and `skills/` into an isolated `HERMES_HOME` and `scripts/` /
        `sub_agents/` into a workspace overlay; Hermes reads `SOUL.md` and `skills/` natively.
        """
        if spec.install_mode != "hermes_home":
            raise ValueError(
                f"install_mode {spec.install_mode!r} is not the hermes mode; this substrate "
                f"has exactly one: 'hermes_home'"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(HERMES_SURFACE, tree_dir):
            raise ValueError(f"{tree_dir} is not a hermes harness tree (no SOUL.md)")
        staged = Path(tempfile.mkdtemp(prefix="adapt-hm-home-"))
        home_dir = staged / "hermes_home"
        ws_dir = staged / "workspace"
        try:
            home_dir.mkdir(parents=True, exist_ok=True)
            ws_dir.mkdir(parents=True, exist_ok=True)
            for entry in HERMES_HOME_ENTRIES:
                src = tree_dir / entry
                if not src.exists():
                    continue
                if src.is_dir():
                    shutil.copytree(src, home_dir / entry)
                else:
                    shutil.copy2(src, home_dir / entry)
            for entry in HERMES_WORKSPACE_ENTRIES:
                src = tree_dir / entry
                if not src.exists():
                    continue
                if src.is_dir():
                    shutil.copytree(src, ws_dir / entry)
                else:
                    shutil.copy2(src, ws_dir / entry)
            for key, body in ctx.items():
                if key.startswith("file:"):
                    rel = HT.normalize_rel(key[len("file:") :])
                    p = ws_dir / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(body, encoding="utf-8")
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        return Install(
            staging_dir=staged,
            fingerprint=RM.fingerprint(tree_dir),
            extras={
                "install_mode": "hermes_home",
                "hermes_home": str(home_dir),
                "workspace_overlay": str(ws_dir),
                **{k: v for k, v in ctx.items() if not k.startswith("file:")},
            },
        )

    def chat_argv(
        self,
        prompt: str,
        workspace: Path,
        model: str = "",
        *,
        reasoning_effort: str = "",
        max_turns: int | None = None,
    ) -> list[str]:
        """Reference `hermes chat` one-shot JSONL argv; benchmark launchers may customize."""
        binary = resolve_hermes_bin() or "hermes"
        argv = [
            binary,
            "chat",
            "-q",
            prompt,
            "--oneshot",
            "--format",
            "stream-json",
            "--in",
            str(workspace),
            "--yolo",
        ]
        if model:
            argv += ["-m", model]
        if reasoning_effort:
            argv += ["--reasoning", reasoning_effort]
        if max_turns is not None:
            argv += ["--max-turns", str(int(max_turns))]
        return argv

    def chat_env(
        self,
        base_env: Mapping[str, str],
        *,
        hermes_home: Path,
        model: str = "",
    ) -> dict[str, str]:
        """Reference isolated environment with `HERMES_HOME` pointed at the staged home."""
        env = config.scrub_env(base_env, keep=config.provider_keys(model or config.SOLVER_MODEL_OC))
        env["HERMES_HOME"] = str(hermes_home)
        return env

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "HermesSubstrate was constructed without a launcher body; the benchmark "
                "adapter must supply one (it owns workspace preparation, model selection, "
                "and read-back locations)"
            )
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 800
    ) -> str:
        return render_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self) -> None:
        """Verify `hermes --version` succeeds on PATH (or `HARNESS2_HERMES_BIN`); starts no model."""
        binary = resolve_hermes_bin()
        if not binary:
            raise RuntimeError(
                "Hermes CLI not found on PATH (and HARNESS2_HERMES_BIN is unset); "
                "install Hermes Agent (`hermes --version`) before running Hermes rollouts"
            )
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ),
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:200]
            raise RuntimeError(f"`{binary} --version` exited {proc.returncode}: {detail}")


_TRUNC_FMT = "…(+{n} chars — render truncation)"


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\x00", "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:n] + _TRUNC_FMT.format(n=len(s) - n)


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 800) -> str:
    """Render `hermes chat -q ... --format stream-json` JSONL into the shared trajectory grammar.

    Hermes `StreamJsonEmitter` emits:
      - `{"type": "system", "subtype": "init", ...}`
      - `{"type": "text", "text": "<delta>"}` (streaming text deltas — coalesced per turn segment)
      - `{"type": "tool_use", "name": "...", "tool_call_id": "...", "input": {...}}`
      - `{"type": "tool_result", "name": "...", "output": "...", "is_error": bool}`
      - `{"type": "result", "exit_code": int, "text": "...", "error": "...", "replay_gap": bool}`
    """
    args_cap = int(args_trunc)
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(failed to read trajectory: {e})"
    raw = raw.replace("\x00", "")

    lines: list[str] = []
    text_buf: list[str] = []
    saw_text_delta = False

    def flush_text() -> None:
        if not text_buf:
            return
        joined = "".join(text_buf).strip()
        text_buf.clear()
        if joined:
            lines.append(f"[ASSISTANT] {_clip(joined, obs_trunc)}")

    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type", "")
        if etype == "system":
            continue
        if etype == "text":
            delta = ev.get("text")
            if isinstance(delta, str) and delta:
                text_buf.append(delta)
                saw_text_delta = True
            continue

        flush_text()
        if etype == "tool_use":
            name = ev.get("name") or "tool"
            tin = ev.get("input")
            args = json.dumps(tin, default=str, ensure_ascii=False) if tin is not None else ""
            lines.append(f"[ACTION] {name}({_clip(args, args_cap)})")
        elif etype == "tool_result":
            output = ev.get("output")
            tag = "OBSERVATION ERROR" if ev.get("is_error") else "OBSERVATION"
            lines.append(f"[{tag}] {_clip(output, obs_trunc)}")
        elif etype == "result":
            final_text = ev.get("text")
            if isinstance(final_text, str) and final_text.strip():
                if ev.get("replay_gap") or not saw_text_delta:
                    lines.append(f"[ASSISTANT] {_clip(final_text.strip(), obs_trunc)}")
            err = ev.get("error")
            exit_code = ev.get("exit_code", 0)
            if isinstance(err, str) and err.strip():
                lines.append(f"[ERROR] {_clip(err.strip(), 300)}")
            elif exit_code not in (0, None):
                lines.append(f"[ERROR] hermes exited with code {exit_code}")

    flush_text()
    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered


__all__ = [
    "HERMES_SURFACE",
    "HermesSubstrate",
    "render_events",
    "resolve_hermes_bin",
    "stage_workspace",
]
