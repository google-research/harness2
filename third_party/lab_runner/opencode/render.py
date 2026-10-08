"""Materialize the base harness into an OpenCode-loadable workspace.

`render()` writes the single-body harness; `render_tree()` writes the eight-component tree,
whose file names match the JobBench reference surface. ENV_DELTA is the only adapter-owned
prose: it corrects the statements in the base system prompt that hold only under the native
sandboxed loop, and it is appended last so it wins where it conflicts.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from harness.run import (
    DEFAULT_SKILLS,
    SKILLS_DIR,
    SYSTEM_PROMPT_PREAMBLE,
    load_skills,
)

BENCH_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_SCRIPTS = Path(__file__).resolve().parent / "scripts"

AGENT_NAME = "lab"

ALWAYS_ON = (
    "systemprompt.md",
    "guardrails.md",
    "LongTermMEMORY.md",
    "tool_descriptions/tool_guidance.md",
)

RESERVED_SCRIPTS = frozenset({"read_doc.py"})

RESERVED_SKILLS = frozenset(DEFAULT_SKILLS)

ENV_DELTA = """\
# Harness notes (this run)

You are running under OpenCode. Three details differ from the description
above — where they conflict, **these win**:

1. **Paths are real and absolute.** `bash` starts in the workspace root, whose
   absolute path is given in the assignment. `documents/`, `output/`, and
   `skills/` are subdirectories of it. There is no path rewriting.

2. **Deliverables are not auto-routed.** Relative `write`/`edit` paths land
   wherever the tool resolves them, *not* in `output/`. Every deliverable must
   be written to `output/<filename>` explicitly (or to its absolute path).
   Anything left outside `output/` is not graded.

3. **`read` does not parse binary documents.** For `.docx`, `.pdf`, `.pptx`,
   and `.xlsx`, run:

       python3 scripts/read_doc.py <path>

   which prints the extracted text. `read` is still the right tool for plain
   text, markdown, and code.
"""


def compose_prompt(skill_names: list[str], *, skill_mode: str = "inline") -> str:
    """Base harness preamble + (optionally) skill manuals + environment delta."""
    parts = [SYSTEM_PROMPT_PREAMBLE.strip()]
    if skill_names and skill_mode == "inline":
        parts.append(load_skills(skill_names).strip())
    parts.append(ENV_DELTA.strip())
    return "\n\n".join(p for p in parts if p) + "\n"


def _copy_skill_scripts(skill_names: list[str], workspace: Path) -> None:
    """Mirror harness.run.setup_skill_scripts — skills/<n>/scripts/ in the workspace."""
    for name in skill_names:
        src = SKILLS_DIR / name / "scripts"
        if src.is_dir():
            shutil.copytree(src, workspace / "skills" / name / "scripts", dirs_exist_ok=True)


def render(
    workspace: Path,
    *,
    skill_names: list[str] | None = None,
    prompt_mode: str = "append",
    skill_mode: str = "inline",
    agent_name: str = AGENT_NAME,
    harness_file: Path | str | None = None,
) -> tuple[str, str | None]:
    """Write the harness material into `workspace`; return (prompt, agent_or_None).

    A None agent name means append mode, and the caller must then not pass `--agent`.
    `harness_file` swaps in a candidate prose body; everything else is materialised as usual.
    """
    if prompt_mode not in ("append", "replace"):
        raise ValueError(f"prompt_mode must be 'append' or 'replace', got {prompt_mode!r}")
    if skill_mode not in ("inline", "native"):
        raise ValueError(f"skill_mode must be 'inline' or 'native', got {skill_mode!r}")

    workspace = Path(workspace)
    skill_names = DEFAULT_SKILLS if skill_names is None else skill_names
    if harness_file is None:
        prompt = compose_prompt(skill_names, skill_mode=skill_mode)
    else:
        body = Path(harness_file).read_text(encoding="utf-8")
        if not body.strip():
            raise ValueError(f"harness file is empty: {harness_file}")
        prompt = body.rstrip("\n") + "\n"

    (workspace / ".opencode").mkdir(parents=True, exist_ok=True)

    active_agent = _write_prompt(workspace, prompt, prompt_mode, agent_name)

    if skill_mode == "native":
        for name in skill_names:
            src = SKILLS_DIR / name / "SKILL.md"
            if src.exists():
                dest = workspace / ".opencode" / "skills" / name
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "SKILL.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    _copy_skill_scripts(skill_names, workspace)

    scripts_dir = workspace / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(ADAPTER_SCRIPTS / "read_doc.py", scripts_dir / "read_doc.py")

    return prompt, active_agent


_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_DOCSTRING = re.compile(r'^\s*(?:"""|\'\'\')[ \t]*(.+)')
_MODE_SUBAGENT = re.compile(r"(?m)^mode:\s*subagent")
_TS_TOOL_KEY = re.compile(r"""(?:^|[\s,{])["']?tool["']?\s*:\s*\{""")
_TS_KEY = re.compile(r"""["']?([A-Za-z_$][A-Za-z0-9_$]*)["']?\s*:""")
_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


@dataclass
class RenderResult:
    """What landed in the workspace, and what the runner has to allowlist for it."""

    prompt: str
    agent: str | None
    mode: str = "base"  # "base" | "file" | "tree"
    base_skills: list[str] = field(default_factory=list)
    harness_skills: list[str] = field(default_factory=list)
    subagents: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    plugin_tools: list[str] = field(default_factory=list)

    @property
    def opencode_only(self) -> bool:
        """True when the harness uses plugins or top-level scripts, which only OpenCode
        loads, so it cannot be rolled out under the native runner."""
        return bool(self.plugins or self.scripts)


def is_tree(path: Path | str) -> bool:
    """A harness directory is a tree iff it has c1; anything else is legacy prose."""
    p = Path(path)
    return p.is_dir() and (p / "systemprompt.md").is_file()


def _strip_comments(text: str) -> str:
    """A component that is ONLY HTML comments renders to nothing; anything else is verbatim.

    Emptying is how the proposer neutralises an always-on file it may not delete.
    """
    if not _COMMENT.sub("", text).strip():
        return ""
    return text.strip()


def _component(harness_dir: Path, rel: str) -> str:
    p = harness_dir / rel
    return _strip_comments(p.read_text(encoding="utf-8", errors="replace")) if p.is_file() else ""


def _script_doc(p: Path) -> str:
    """First line of the module docstring — the pointer text the agent sees."""
    try:
        m = _DOCSTRING.search(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return "helper script"
    return m.group(1).strip().rstrip('"\'')[:120] if m else "helper script"


def _tree_names(harness_dir: Path, pattern: str) -> list[str]:
    """Component names under a glob, e.g. skills/*/SKILL.md -> the skill names."""
    if pattern.endswith(".md"):
        return sorted(p.parent.name for p in harness_dir.glob(pattern))
    return sorted(p.name for p in harness_dir.glob(pattern) if not p.name.startswith("_"))


def _blank_ts_literals(src: str) -> str:
    """Blank string/template/comment *contents*, preserving length and offsets.

    A string whose whole content is a bare identifier is kept: it is a quoted object key.
    """
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == c:
                    break
                j += 1
            if not _IDENT.fullmatch(src[i + 1:j]):
                for k in range(i + 1, min(j, n)):
                    if src[k] != "\n":
                        out[k] = " "
            i = j + 1
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if src[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def plugin_tool_names(path: Path | str) -> list[str]:
    """Tool ids a plugin registers — the keys of its `tool: { … }` object.

    A plugin's hooks run unconditionally, but a tool it registers goes through the same
    `tools` gate as a built-in one, so its name has to reach the runner's allowlist.
    """
    path = Path(path)
    try:
        src = _blank_ts_literals(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    names: list[str] = []
    for m in _TS_TOOL_KEY.finditer(src):
        depth, i, n = 0, src.index("{", m.start()), len(src)
        block: list[str] = []
        while i < n:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    names.extend(block)  # only a CLOSED block is trustworthy
                    break
            elif depth == 1:
                km = _TS_KEY.match(src, i)
                if km and src[i - 1] in "{,\n \t":
                    block.append(km.group(1))
                    i = km.end()
                    continue
            i += 1
    seen: set[str] = set()
    return [x for x in names if not (x in seen or seen.add(x))]


def _ensure_mode_subagent(text: str) -> str:
    """OpenCode only treats an agent file as delegable when it says so."""
    if _MODE_SUBAGENT.search(text):
        return text
    if text.lstrip().startswith("---"):
        return re.sub(r"^\s*---\n", "---\nmode: subagent\n", text, count=1)
    return "---\nmode: subagent\ndescription: delegated subagent\n---\n\n" + text


def compose_body(
    harness_dir: Path | str,
    *,
    base_skill_names: list[str] | None = None,
    skill_mode: str = "inline",
) -> str:
    """The always-on prompt body for a harness tree, with ENV_DELTA appended last."""
    harness_dir = Path(harness_dir)
    base_skill_names = DEFAULT_SKILLS if base_skill_names is None else base_skill_names
    parts: list[str] = []

    if sp := _component(harness_dir, "systemprompt.md"):
        parts.append(sp)
    if gr := _component(harness_dir, "guardrails.md"):
        parts.append("# Operating guardrails\n\n" + gr)
    if tg := _component(harness_dir, "tool_descriptions/tool_guidance.md"):
        parts.append(tg)
    if mem := _component(harness_dir, "LongTermMEMORY.md"):
        parts.append("# Long-term memory\n\n" + mem)

    scripts = sorted(
        p for p in (harness_dir / "scripts").glob("*.py") if not p.name.startswith("_")
    )
    if scripts:
        listing = "\n".join(f"- `python3 scripts/{p.name}` — {_script_doc(p)}" for p in scripts)
        parts.append(
            "# Helper scripts (run via bash)\n\nDeterministic helpers live in `./scripts`. "
            "Prefer them over hand-rolling the same logic:\n\n" + listing
        )

    if base_skill_names and skill_mode == "inline":
        parts.append(load_skills(base_skill_names).strip())

    parts.append(ENV_DELTA.strip())
    return "\n\n".join(p for p in parts if p.strip()) + "\n"


def _write_prompt(workspace: Path, prompt: str, prompt_mode: str, agent_name: str) -> str | None:
    if prompt_mode == "replace":
        agent_dir = workspace / ".opencode" / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / f"{agent_name}.md").write_text(
            "---\nmode: primary\ndescription: Harvey LAB legal work agent\n---\n\n" + prompt,
            encoding="utf-8",
        )
        return agent_name
    (workspace / "AGENTS.md").write_text(prompt, encoding="utf-8")
    return None


def render_tree(
    workspace: Path | str,
    harness_dir: Path | str | None = None,
    *,
    base_skill_names: list[str] | None = None,
    prompt_mode: str = "append",
    # native: the manuals load on demand instead of dominating the always-on body.
    skill_mode: str = "native",
    agent_name: str = AGENT_NAME,
) -> RenderResult:
    """Render a harness tree — or a legacy single body — into `workspace`.

      harness_dir=None                       -> the base harness (mode "base")
      a file, or a dir with only AGENTS.md   -> legacy single body, used verbatim
      a dir with systemprompt.md             -> tree (mode "tree")

    Raises ValueError for collisions this renderer would otherwise silently clobber.
    """
    if prompt_mode not in ("append", "replace"):
        raise ValueError(f"prompt_mode must be 'append' or 'replace', got {prompt_mode!r}")
    if skill_mode not in ("inline", "native"):
        raise ValueError(f"skill_mode must be 'inline' or 'native', got {skill_mode!r}")

    workspace = Path(workspace)
    base_skills = DEFAULT_SKILLS if base_skill_names is None else base_skill_names

    legacy_file: Path | None = None
    if harness_dir is not None:
        harness_dir = Path(harness_dir)
        if harness_dir.is_file():
            legacy_file = harness_dir
        elif not is_tree(harness_dir):
            legacy_file = harness_dir / "AGENTS.md"
            if not legacy_file.is_file():
                raise ValueError(
                    f"{harness_dir} is neither a tree (no systemprompt.md) nor legacy "
                    "(no AGENTS.md)"
                )
    if harness_dir is None or legacy_file is not None:
        prompt, agent = render(
            workspace,
            skill_names=base_skills,
            prompt_mode=prompt_mode,
            skill_mode=skill_mode,
            agent_name=agent_name,
            harness_file=legacy_file,
        )
        return RenderResult(
            prompt=prompt,
            agent=agent,
            mode="file" if legacy_file else "base",
            base_skills=list(base_skills),
        )

    harness_dir = Path(harness_dir)
    skills = _tree_names(harness_dir, "skills/*/SKILL.md")
    subagents = _tree_names(harness_dir, "sub_agents/*/AGENT.md")
    scripts = _tree_names(harness_dir, "scripts/*.py")
    plugins = _tree_names(harness_dir, "plugin/*.ts")

    if clash := sorted(set(skills) & RESERVED_SKILLS):
        raise ValueError(
            f"harness skills collide with base skills {clash}: OpenCode resolves duplicate "
            f"skill names nondeterministically, so the run would differ from itself"
        )
    if clash := sorted(set(scripts) & RESERVED_SCRIPTS):
        raise ValueError(f"harness scripts use reserved names {clash} (see ENV_DELTA)")
    if prompt_mode == "replace" and agent_name in subagents:
        raise ValueError(f"subagent '{agent_name}' would overwrite the primary agent file")

    prompt = compose_body(harness_dir, base_skill_names=base_skills, skill_mode=skill_mode)
    if not prompt.strip():
        raise ValueError(f"harness tree composes to an empty prompt: {harness_dir}")

    (workspace / ".opencode").mkdir(parents=True, exist_ok=True)
    agent = _write_prompt(workspace, prompt, prompt_mode, agent_name)

    for name in skills:
        dest = workspace / ".opencode" / "skills" / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness_dir / "skills" / name / "SKILL.md", dest / "SKILL.md")
        src_scripts = harness_dir / "skills" / name / "scripts"
        if src_scripts.is_dir():
            scripts_dest = workspace / "skills" / name / "scripts"
            shutil.copytree(src_scripts, scripts_dest, dirs_exist_ok=True)

    if subagents:
        agent_dir = workspace / ".opencode" / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        for name in subagents:
            text = (harness_dir / "sub_agents" / name / "AGENT.md").read_text(
                encoding="utf-8", errors="replace"
            )
            (agent_dir / f"{name}.md").write_text(_ensure_mode_subagent(text), encoding="utf-8")

    scripts_dir = workspace / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in scripts:
        shutil.copy(harness_dir / "scripts" / name, scripts_dir / name)

    plugin_tools: list[str] = []
    if plugins:
        plugin_dir = workspace / ".opencode" / "plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        for name in plugins:
            src = harness_dir / "plugin" / name
            shutil.copy(src, plugin_dir / name)
            plugin_tools.extend(plugin_tool_names(src))

    if skill_mode == "native":
        for name in base_skills:
            src = SKILLS_DIR / name / "SKILL.md"
            if src.exists():
                dest = workspace / ".opencode" / "skills" / name
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dest / "SKILL.md")

    _copy_skill_scripts(base_skills, workspace)
    shutil.copy(ADAPTER_SCRIPTS / "read_doc.py", scripts_dir / "read_doc.py")

    seen: set[str] = set()
    return RenderResult(
        prompt=prompt,
        agent=agent,
        mode="tree",
        base_skills=list(base_skills),
        harness_skills=skills,
        subagents=subagents,
        scripts=scripts,
        plugins=plugins,
        plugin_tools=[t for t in plugin_tools if not (t in seen or seen.add(t))],
    )
