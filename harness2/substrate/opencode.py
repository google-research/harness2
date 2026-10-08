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

"""OpencodeSubstrate: the OpenCode agent framework as a solver substrate.

Carries the c1..c8 surface (OPENCODE_SURFACE), the three install modes, the bun-run CLI
contract, and the one opencode event-stream trajectory renderer. The install modes are
different delivery channels for the always-on text and are not interchangeable: changing the
one a benchmark uses moves that benchmark's baseline.

    workspace_agents_md   JB   AGENTS.md append + .opencode/{skills,agent} + scripts
    home_dir_jinja        WB   _composed/prompt.j2 + a component map for the container agent
    workspace_config      LAB  render composition + ENV_DELTA + the opencode.json tool gate
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Mapping

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest

OPENCODE_SURFACE = SurfaceSpec(
    name="opencode",
    always_on=(
        "systemprompt.md",
        "guardrails.md",
        "LongTermMEMORY.md",
        "tool_descriptions/tool_guidance.md",
    ),
    always_on_max=24_000,
    always_on_cap_reason="every token here competes with the agent's own output budget",
    skill_dir="skills",
    skill_name_rule="must_match",
    trigger_hint=(
        "Fix it by adding a trigger line naming `{name}` to systemprompt.md or "
        'guardrails.md, e.g. "When <the situation this skill handles>, load the '
        '`{name}` skill."'
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
            "<!-- c5: delegated subagents, invoked with the `task` tool in their OWN context. "
            "Add one as sub_agents/<name>/AGENT.md with frontmatter `mode: subagent` and "
            "`description:`. -->\n",
        ),
        (
            "plugin/README.md",
            "<!-- c8: OpenCode plugins. Acts INSIDE the tool loop -- the only lever that can "
            "enforce something the model cannot skip. Add one as plugin/<name>.ts. -->\n",
        ),
        (
            "skills/README.md",
            "<!-- c6: reusable workflows loaded on demand via the `skill` tool. Add one as "
            "skills/<name>/SKILL.md with frontmatter `name:` matching the directory and a "
            "`description:`. -->\n",
        ),
    ),
)


OUTPUT_TOKEN_MAX = 64_000

_OC_INSTALL_MODES = ("workspace_agents_md", "home_dir_jinja", "workspace_config")

_JINJA_PLACEHOLDER = "{{ instruction }}"
_BRACE = re.compile(r"\{\{|\{%|\{#")


def assert_jinja_safe(composed: str) -> None:
    """Enforce the Jinja invariant for a composed home_dir_jinja template: exactly one
    `{{ instruction }}` placeholder, at the very end, and no other brace pair anywhere."""
    n = composed.count(_JINJA_PLACEHOLDER)
    if n != 1:
        raise ValueError(
            f"composed template must contain the '{{{{ instruction }}}}' placeholder exactly "
            f"once (found {n}); Harbor renders it with StrictUndefined and substitutes the "
            f"task instruction there, so a literal placeholder in always-on text would "
            f"inject the task twice"
        )
    without = composed.replace(_JINJA_PLACEHOLDER, "", 1)
    m = _BRACE.search(without)
    if m:
        raise ValueError(
            f"always-on text or a skill/subagent description contains a Jinja brace pair "
            f"({m.group(0)!r}); the composed prompt template renders with StrictUndefined, so "
            f"any braces other than the one '{{{{ instruction }}}}' placeholder kill the job "
            f"pre-trial (and '{{#' comments delete text silently). Use a single brace, or "
            f"words."
        )
    if not composed.rstrip().endswith(_JINJA_PLACEHOLDER):
        raise ValueError(
            "composed template must END with the instruction placeholder "
            "(StrictUndefined render contract)"
        )


def _frontmatter_desc(body: str) -> str:
    m = re.search(r"^description:\s*(.+)$", body[:600], re.M)
    return m.group(1).strip() if m else "(no description)"


def _component_index(staged: Path, pattern: str) -> str:
    """One "- name: description" line per component matching `pattern`, or ""."""
    found = sorted(staged.glob(pattern), key=lambda p: p.parent.name)
    return "\n".join(
        f"- `{p.parent.name}`: {_frontmatter_desc(p.read_text(errors='replace'))}" for p in found
    )


def compose_prompt(staged: Path) -> str:
    """Build the Jinja template that carries the always-on harness into a WB run.

    Skills and subagents are advertised by name and description; scripts are named at the
    path the installer puts them. Ends with the `{{ instruction }}` placeholder.
    """
    parts = [
        t
        for f in OPENCODE_SURFACE.always_on
        if (staged / f).is_file() and (t := (staged / f).read_text(errors="replace").strip())
    ]
    idx = _component_index(staged, "skills/*/SKILL.md")
    if idx:
        parts.append(
            f"# AVAILABLE SKILLS\nLoad with the `skill` tool when the situation matches:\n{idx}"
        )
    idx = _component_index(staged, "sub_agents/*/AGENT.md")
    if idx:
        parts.append(
            "# AVAILABLE SUBAGENTS\nDelegate with the `task` tool when the "
            f"situation matches:\n{idx}"
        )
    scripts = sorted(p.name for p in staged.glob("scripts/*.py"))
    if scripts:
        parts.append(
            f"# HELPER SCRIPTS\nAvailable at `~/harness_scripts/`: "
            f"{', '.join(scripts)}\n"
            "Run with `python ~/harness_scripts/<name>.py`."
        )
    parts.append(_JINJA_PLACEHOLDER)
    return "\n\n---\n\n".join(parts)


class OpencodeSubstrate:
    """OpenCode behind the one Substrate interface; the launcher body is benchmark-supplied."""

    name = "oc"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return OPENCODE_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        mode = spec.install_mode
        if mode not in _OC_INSTALL_MODES:
            raise ValueError(
                f"install_mode {mode!r} is not an opencode mode; this substrate supports "
                f"{_OC_INSTALL_MODES} (codex_root belongs to CodexSubstrate)"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(OPENCODE_SURFACE, tree_dir):
            raise ValueError(
                f"{tree_dir} is not an opencode harness tree (no always-on file present)"
            )
        staged = Path(tempfile.mkdtemp(prefix=f"adapt-oc-{mode}-"))
        try:
            shutil.copytree(tree_dir, staged, dirs_exist_ok=True)

            for key, body in ctx.items():
                if key.startswith("file:"):
                    rel = HT.normalize_rel(key[len("file:") :])
                    p = staged / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(body, encoding="utf-8")

            if mode == "home_dir_jinja":
                composed = compose_prompt(staged)

                tg = staged / "tool_descriptions" / "tool_guidance.md"
                if tg.is_file():
                    shutil.move(str(tg), staged / "tool_guidance.md")
                    shutil.rmtree(staged / "tool_descriptions", ignore_errors=True)
                assert_jinja_safe(composed)
                out = staged / "_composed" / "prompt.j2"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(composed, encoding="utf-8")
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

        The upstream checkout runs from source through bun: there is no `opencode` binary in
        the contract. With `agent=""` the rendered AGENTS.md appends to the provider prompt.
        """
        argv = [
            str(config.BUN),
            "run",
            "--cwd",
            str(config.OPENCODE_DIR / "packages" / "opencode"),
            "--conditions=browser",
            "src/index.ts",
            "run",
            prompt,
            "--dir",
            str(workspace),
            "--model",
            model,
            "--title",
            title,
            "--format",
            "json",
        ]
        if agent:
            argv += ["--agent", agent]
        if variant:
            argv += ["--variant", variant]
        if session:
            argv += ["--session", session]
        return argv

    def run_env(
        self,
        base_env: Mapping[str, str],
        workspace: Path,
        *,
        output_token_max: int = OUTPUT_TOKEN_MAX,
        extra_allowed: tuple[str, ...] = (),
    ) -> dict[str, str]:
        """The reference permission fence and output-token ceiling; see run_argv. Without
        OPENCODE_PERMISSION the session auto-rejects reads of its own working tree."""
        allow = {"*": "deny", str(workspace): "allow", f"{workspace}/**": "allow"}
        for root in extra_allowed:
            allow[str(root)] = "allow"
            allow[f"{root}/**"] = "allow"
        env = dict(base_env)
        env["OPENCODE_PERMISSION"] = json.dumps({"external_directory": allow})
        env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"] = str(int(output_token_max))
        return env

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "OpencodeSubstrate was constructed without a launcher body; the benchmark "
                "adapter must supply one (it owns runner choice, env wiring and read-back "
                "locations)"
            )
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400
    ) -> str:
        return render_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self) -> None:
        """Checks the OpenCode checkout and bun exist; starts nothing."""
        oc = config.OPENCODE_DIR
        if not (oc / "packages" / "opencode" / "src" / "index.ts").is_file():
            raise RuntimeError(
                f"OpenCode checkout missing at {oc} (no packages/opencode/src/index.ts): "
                f"run scripts/setup_benchmark.py <benchmark> to fetch the pinned checkout "
                f"at {config.DEPENDENCY_PINS['opencode'][:12]} (v1.14.18)."
            )
        if not config.BUN.is_file():
            raise RuntimeError(
                f"bun not found at {config.BUN}: install bun on PATH or at ~/.bun/bin/bun "
                f"(the upstream OpenCode is run from source through bun)."
            )


_TRUNC_FMT = "…(+{n} chars — render truncation)"


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\x00", "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:n] + _TRUNC_FMT.format(n=len(s) - n)


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400) -> str:
    """Render OpenCode's `--format json` JSONL event stream into the shared grammar.

    Parsing is defensive because the event schema varies by OpenCode version.
    """
    text_tags = {"text": "ASSISTANT", "reasoning": "THINKING"}
    args_cap = int(args_trunc)
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(failed to read trajectory: {e})"
    lines: list[str] = []
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
        err = ev.get("error")
        if isinstance(err, dict):
            msg = (
                (err.get("data") or {}).get("message")
                or err.get("message")
                or json.dumps(err)[:300]
            )
            lines.append(f"[ERROR] {_clip(msg, 300)}")
            continue
        part = ev.get("part") if isinstance(ev.get("part"), dict) else ev
        ptype = part.get("type", ev.get("type"))
        if ptype in text_tags:
            txt = part.get("text") or part.get("content")
            if isinstance(txt, str) and txt.strip():
                lines.append(f"[{text_tags[ptype]}] {_clip(txt, obs_trunc)}")
        elif ptype in ("tool", "tool_use", "tool_call", "tool-invocation"):
            name = (
                part.get("tool")
                or ev.get("tool")
                or part.get("name")
                or (part.get("toolInvocation") or {}).get("toolName")
                or "tool"
            )
            state = part.get("state") if isinstance(part.get("state"), dict) else part
            status = state.get("status", "")
            tin = state.get("input") or part.get("input") or part.get("args")
            tout = state.get("output") or state.get("result") or part.get("output")
            args = json.dumps(tin, default=str, ensure_ascii=False) if tin is not None else ""
            lines.append(f"[ACTION] {name}({_clip(args, args_cap)})")
            if status == "error":
                lines.append(f"[OBSERVATION ERROR] {_clip(state.get('error'), obs_trunc)}")
            elif tout is not None:
                lines.append(f"[OBSERVATION] {_clip(tout, obs_trunc)}")
    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered
