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

"""PiSubstrate: the Pi coding agent (config.PI_VERSION) as a solver substrate.

Carries the c1..c8 surface (PI_SURFACE), the one install mode (pi_root: AGENTS.md, .agents,
.pi and scripts copied to the workspace root, where `pi` run in that directory finds them),
the `--mode json` flag contract, and the event-stream trajectory renderer. Pi has no
subagents; its in-loop lever is a TypeScript extension under .pi/extensions/.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest, read_events, stage_entries
from .codex import _TRUNC_FMT, CODEX_SURFACE, _clip, _emit_if, _text_of

PI_SURFACE = SurfaceSpec(
    name="pi",
    always_on=("AGENTS.md",),
    always_on_max=8_000,
    always_on_cap_reason="AGENTS.md is added whole to the system prompt of EVERY turn",
    skill_dir=".agents/skills",
    skill_name_rule="overrides_dir",
    trigger_hint=CODEX_SURFACE.trigger_hint,
    autotrigger_file="AGENTS.md",
    trigger_line=CODEX_SURFACE.trigger_line,
    subagent_requires_mode=False,
    plugin_dir=".pi/extensions",
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
            # Not inside .pi/extensions/: Pi loads every entry there as an extension.
            ".pi/README.md",
            "<!-- c8: Pi extensions -- the only lever that acts INSIDE the tool loop, so the "
            "model cannot skip it. Add one as .pi/extensions/<name>.ts exporting "
            "`default function (pi) {...}`; a `tool_call` handler returning "
            "{ block: true, reason } stops the call and feeds the reason back to the model. -->\n",
        ),
        (
            ".agents/skills/README.md",
            "<!-- c6: reusable workflows. Add one as .agents/skills/<name>/SKILL.md with YAML "
            "frontmatter: `description:` is REQUIRED (no description = skill not loaded). Only "
            "name+description are advertised to the agent; the body is read on demand. -->\n",
        ),
    ),
)


PI_ROOT_ENTRIES = ("AGENTS.md", ".agents", ".pi", "scripts")


class PiSubstrate:
    """The Pi coding agent behind the one Substrate interface."""

    name = "pi"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return PI_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage the pi_root entries for a copy to the workspace root; no render step."""
        if spec.install_mode != "pi_root":
            raise ValueError(
                f"install_mode {spec.install_mode!r} is not the pi mode; this substrate "
                f"has exactly one: 'pi_root'"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(PI_SURFACE, tree_dir):
            raise ValueError(f"{tree_dir} is not a pi harness tree (no AGENTS.md)")
        return Install(
            staging_dir=stage_entries(tree_dir, PI_ROOT_ENTRIES, "adapt-pi-root-"),
            fingerprint=RM.fingerprint(tree_dir),
            extras={
                "install_mode": "pi_root",
                **{k: v for k, v in ctx.items() if not k.startswith("file:")},
            },
        )

    def exec_argv(self, prompt: str, workspace: Path, model: str) -> list[str]:
        """The headless argv; run it with cwd=workspace (pi has no directory flag).

        `--approve` trusts the workspace so its .pi/extensions load. Skills come only from the
        workspace: pi otherwise also loads $HOME/.agents/skills, whatever the agent dir.
        """
        return [
            str(config.PI_BIN),
            "--mode",
            "json",
            "--no-session",
            "--approve",
            "--no-skills",
            "--skill",
            str(workspace / ".agents" / "skills"),
            "--model",
            model,
            "--",
            prompt,
        ]

    def exec_env(self, base_env: Mapping[str, str], *, config_dir: Path) -> dict[str, str]:
        """A throwaway agent dir, so the host's ~/.pi settings, keys and skills never leak.

        HARNESS2_* (judge/codex/bridge keys) is dropped; provider keys stay, pi may need them.
        """
        return {
            **{k: v for k, v in base_env.items() if not k.startswith("HARNESS2_")},
            "PI_CODING_AGENT_DIR": str(config_dir),
            "PI_SKIP_VERSION_CHECK": "1",
            "PI_TELEMETRY": "0",
        }

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "PiSubstrate was constructed without a launcher body; the benchmark "
                "adapter must supply one"
            )
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def run_failed(self, stream_path: Path) -> bool:
        """Whether the last assistant message ended in an error; pi still exits 0 then."""
        ends = [
            e["message"]
            for e in read_events(Path(stream_path))
            if e.get("type") == "message_end"
            and isinstance(e.get("message"), dict)
            and e["message"].get("role") == "assistant"
        ]
        return bool(ends) and ends[-1].get("stopReason") in ("error", "aborted")

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000
    ) -> str:
        return render_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self) -> None:
        """Ask pi itself, without a model call, whether it can run PI_MODEL exactly.

        One `--list-models` call checks Node.js, the binary, that credentials are configured
        (present, not validated) and the exact id: pi lists only models it has auth for, and
        silently fuzzy-matches an id it cannot find.
        """
        if not config.PI_MODEL:
            raise config.ConfigError("set HARNESS2_PI_MODEL (or HARNESS2_SOLVER_MODEL)")
        if not config.PI_BIN.is_file():
            raise RuntimeError("Pi is not installed; run harness2-setup <bench> --substrate pi")
        with tempfile.TemporaryDirectory() as home:
            p = subprocess.run(
                [str(config.PI_BIN), "--list-models"],
                env=self.exec_env(os.environ, config_dir=Path(home)),
                capture_output=True,
                text=True,
                timeout=120,
            )
        if config.PI_MODEL.split("/", 1) not in [ln.split()[:2] for ln in p.stdout.splitlines()]:
            raise RuntimeError(
                f"pi cannot run {config.PI_MODEL!r}: it is not in `pi --list-models` (needs "
                f"Node.js >= 22.19, Vertex credentials and an exact provider/model id). "
                f"{(p.stderr or p.stdout).strip()[-300:]}"
            )


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000) -> str:
    """Render Pi's `--mode json` event stream into the shared grammar.

    Pi exits 0 even when the model call fails, so a failed final message is surfaced as
    [ERROR] here; tool calls are rendered from the execution events, not the message.
    """
    lines: list[str] = []
    for ev in read_events(Path(path)):
        et = ev.get("type")
        if et == "_read_error":
            return f"(failed to read trajectory: {ev.get('message')})"
        if et == "tool_execution_start":
            args = json.dumps(ev.get("args"), ensure_ascii=False, default=str)
            lines.append(f"[ACTION] {ev.get('toolName')}({_clip(args, args_trunc)})")
        elif et == "tool_execution_end":
            result = ev.get("result") if isinstance(ev.get("result"), dict) else {}
            tag = "OBSERVATION ERROR" if ev.get("isError") else "OBSERVATION"
            lines.append(f"[{tag}] {_clip(_text_of(result.get('content')), obs_trunc)}")
        elif et == "message_end" and isinstance(ev.get("message"), dict):
            msg = ev["message"]
            if msg.get("role") == "user":
                _emit_if(lines, "USER", _text_of(msg.get("content")), obs_trunc)
            elif msg.get("role") == "assistant":
                for b in msg.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        _emit_if(lines, "ASSISTANT", b.get("text") or "", obs_trunc)
                    elif b.get("type") == "thinking":
                        _emit_if(lines, "REASONING", b.get("thinking") or "", obs_trunc)
                if msg.get("stopReason") in ("error", "aborted", "length"):
                    reason = msg.get("errorMessage") or ""
                    lines.append(f"[ERROR] {msg['stopReason']}: {_clip(reason, 1000)}")
        elif et in ("auto_retry_start", "compaction_start"):
            detail = ev.get("errorMessage") or ev.get("reason") or ""
            lines.append(f"[HARNESS] {et} {_clip(detail, 1000)}".rstrip())
    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered
