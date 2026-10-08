"""Run one LAB task through OpenCode.

    uv run python -m opencode.run_opencode \
        --model google-vertex/gemini-3.5-flash \
        --task corporate-ma/review-data-room-red-flag-review

Mirrors `harness.run` — same task loading, same prompt and skills, same results/<run_id>/
layout, so `run_eval` scores an OpenCode run unchanged. What differs is the loop: OpenCode
owns it and its own tools, on the host, with `OPENCODE_PERMISSION` fenced to the workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from harness.run import BENCH_ROOT, DEFAULT_SKILLS, _load_env, load_task
from harness.tools import get_all_tool_definitions
from opencode import session as oc_session
from opencode.render import RenderResult, render_tree
from utils.stdio import force_utf8_stdio

DEFAULT_OPENCODE_DIR = Path(os.environ.get("OPENCODE_DIR", ""))
DEFAULT_BUN = Path.home() / ".bun" / "bin" / "bun"

# OpenCode caps output at 32k and the thinking budget eats that cap, truncating turns
# before the deliverable is written. Raise to the model's real output limit.
OUTPUT_TOKEN_MAX = "64000"

NUDGE = (
    "Continue the assignment to completion now. Do not ask for confirmation. "
    "If every deliverable has already been written to the output directory, "
    "reply with exactly: DONE"
)


def build_prompt(task: dict, workspace: Path) -> str:
    """The assignment message: the task instructions verbatim plus the concrete paths."""
    return (
        f"=== WORKSPACE ===\n{workspace}\n\n"
        f"=== DOCUMENTS (read-only) ===\n{workspace / 'documents'}\n\n"
        f"=== OUTPUT DIRECTORY (deliverables go here) ===\n{workspace / 'output'}\n\n"
        f"=== ASSIGNMENT ===\n{task['instructions']}"
    )


def base_tool_names() -> set[str]:
    """The tool names the native harness exposes, read from harness/tools.py."""
    return {t["name"] for t in get_all_tool_definitions()}


def tool_map(tool_mode: str, skill_mode: str,
             extra_tools: Iterable[str] = (),
             plugin_tools: Iterable[str] = ()) -> dict[str, bool] | None:
    """opencode.json `tools` gate. None means "leave OpenCode's set alone".

    `base` denies everything and re-allows only what harness/tools.py defines, keeping the
    run tool-matched to the native harness; `extra_tools` and the tools a c8 plugin
    registers are re-allowed on top, and are recorded in config.json as `tools_allowed`.
    """
    if tool_mode == "native":
        return None
    # "*" first: the ruleset resolves last-match-wins, so the allows below it win.
    gate: dict[str, bool] = {"*": False}
    for name in sorted(base_tool_names()):
        gate[name] = True
    if skill_mode == "native":
        gate["skill"] = True
    for name in sorted({*extra_tools, *plugin_tools}):
        gate[name] = True
    return gate


def tree_extra_tools(rendered: RenderResult) -> list[str]:
    """OpenCode tools a rendered harness tree needs beyond the base set.

    `skill` is the only way an on-demand SKILL.md is read and `task` the only way a
    subagent is reached, so both follow from what was rendered.
    """
    return ([] if not rendered.harness_skills else ["skill"]) + \
           ([] if not rendered.subagents else ["task"])


def harness_inventory(rendered: RenderResult) -> dict:
    """The inventory of components that were live in this run, for the run artifacts."""
    return {
        "mode": rendered.mode,
        "always_on_chars": len(rendered.prompt),
        "base_skills": list(rendered.base_skills),
        "harness_skills": list(rendered.harness_skills),
        "subagents": list(rendered.subagents),
        "scripts": list(rendered.scripts),
        "plugins": list(rendered.plugins),
        "plugin_tools": list(rendered.plugin_tools),
        "opencode_only": rendered.opencode_only,
    }


def component_use(summary: dict, rendered: RenderResult) -> dict:
    """How often each on-demand component actually fired, per tool call."""
    names = [c["name"] for c in summary["tool_calls"]]
    bash_blobs = [json.dumps(c["input"]) for c in summary["tool_calls"] if c["name"] == "bash"]
    return {
        "skill_calls": names.count("skill"),
        "task_calls": names.count("task"),
        "plugin_tool_calls": {t: names.count(t) for t in rendered.plugin_tools},
        "script_calls": {s: sum(f"scripts/{s}" in b for b in bash_blobs)
                         for s in rendered.scripts},
        "write_edit_calls": sum(names.count(t) for t in ("write", "edit", "multiedit",
                                                        "apply_patch", "patch")),
    }


def write_config(workspace: Path, *, agent: str | None, max_turns: int, prompt: str,
                 reasoning_effort: str | None, tools: dict[str, bool] | None) -> None:
    """opencode.json for this run."""
    agent_cfg: dict = {"mode": "primary", "steps": max_turns}
    if reasoning_effort:
        agent_cfg["options"] = {
            "thinkingConfig": {"thinkingLevel": reasoning_effort, "includeThoughts": True}
        }
    if agent:
        agent_cfg["prompt"] = prompt

    # A blanket permission allow overrides ``tools: {'*': false}``, so both layers carry
    # the same ordered deny-then-allow rules.
    permission = ({name: ("allow" if allowed else "deny")
                   for name, allowed in tools.items()}
                  if tools is not None else {"*": "allow"})
    config: dict = {
        "$schema": "https://opencode.ai/config.json",
        "permission": permission,
        "snapshot": False,
    }
    if tools is not None:
        config["tools"] = tools
    if agent:
        config["agent"] = {agent: agent_cfg}
    elif reasoning_effort:
        config["agent"] = {"build": {"options": agent_cfg["options"]}}

    (workspace / "opencode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


def build_env(workspace: Path, doc_parser: str) -> dict:
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(workspace / ".ocdata")
    env["OPENCODE_PERMISSION"] = json.dumps(
        {"external_directory": {"*": "deny", str(workspace): "allow", f"{workspace}/**": "allow"}}
    )
    env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"] = OUTPUT_TOKEN_MAX
    env["LAB_DOC_PARSER"] = doc_parser
    env["LAB_BENCH_ROOT"] = str(BENCH_ROOT)
    # Match the location selected by the deployment configuration.
    env.setdefault("GOOGLE_VERTEX_LOCATION", env.get("GOOGLE_CLOUD_LOCATION", "global"))
    env.setdefault("VERTEXAI_LOCATION", env.get("GOOGLE_CLOUD_LOCATION", "global"))
    return env


def invoke(args_extra: list[str], *, base: list[str], env: dict, timeout: int,
           session_path: Path, log_path: Path, append: bool) -> int:
    """One `opencode run`, appending its JSON stream to session_path."""
    with open(session_path, "a" if append else "w", encoding="utf-8") as out, \
         open(log_path, "a", encoding="utf-8") as err:
        proc = subprocess.run(
            ["timeout", "--kill-after=30s", str(int(max(30, timeout)))] + base + args_extra,
            stdout=out, stderr=err, env=env, cwd=str(BENCH_ROOT),
        )
    return proc.returncode


def main(args) -> None:
    force_utf8_stdio()
    _load_env()

    if args.harness_file and args.harness_dir:
        sys.exit("--harness-file and --harness-dir are mutually exclusive")

    if args.run_id is None:
        model_short = args.model.split("/")[-1].replace(".", "-")
        effort_suffix = f"-{args.reasoning_effort}" if args.reasoning_effort else ""
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.run_id = f"{args.task}/opencode-{model_short}{effort_suffix}/{ts}"

    print(f"Loading task: {args.task}")
    task = load_task(task_name=args.task)

    results_dir = Path(args.results_root or BENCH_ROOT / "results") / args.run_id
    workspace = results_dir / "workspace"
    output_dir = workspace / "output"
    docs_dest = workspace / "documents"
    workspace.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if docs_dest.exists():
        shutil.rmtree(docs_dest)
    shutil.copytree(task["docs_dir"], docs_dest)
    for p in docs_dest.rglob("*"):
        if p.is_file():
            p.chmod(0o444)

    skill_names = DEFAULT_SKILLS if args.skills is None else args.skills
    rendered = render_tree(
        workspace,
        args.harness_dir or args.harness_file,
        base_skill_names=skill_names,
        prompt_mode=args.prompt_mode,
        skill_mode=args.skill_mode,
    )
    if rendered.subagents and not args.allow_subagents:
        sys.exit(f"harness defines subagents {rendered.subagents} but --allow-subagents was not "
                 f"passed; `task` has no native-harness equivalent, so the run would not be "
                 f"tool-matched to the baselines")
    prompt, agent = rendered.prompt, rendered.agent
    components = harness_inventory(rendered)
    tools = tool_map(args.tool_mode, args.skill_mode,
                     list(args.extra_tools) + tree_extra_tools(rendered),
                     rendered.plugin_tools)
    write_config(workspace, agent=agent, max_turns=args.max_turns, prompt=prompt,
                 reasoning_effort=args.reasoning_effort, tools=tools)

    config = {
        "runner": "opencode",
        "model": args.model,
        "task": args.task,
        "run_id": args.run_id,
        "max_turns": args.max_turns,
        "reasoning_effort": args.reasoning_effort,
        "skills": skill_names,
        "prompt_mode": args.prompt_mode,
        "skill_mode": args.skill_mode,
        "tool_mode": args.tool_mode,
        "tools_allowed": sorted(k for k, v in (tools or {}).items() if v) or "opencode-default",
        "doc_parser": args.doc_parser,
        "opencode_dir": str(args.opencode_dir),
        "harness_components": components,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.harness_file:
        config["harness_file"] = str(args.harness_file)
    if args.harness_dir:
        config["harness_dir"] = str(args.harness_dir)
    (results_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    oc_pkg = Path(args.opencode_dir) / "packages" / "opencode"
    if not oc_pkg.is_dir():
        sys.exit(f"OpenCode not found at {oc_pkg} — pass --opencode-dir")

    base = [str(args.bun), "run", "--cwd", str(oc_pkg), "--conditions=browser", "src/index.ts",
            "run", "--dir", str(workspace), "--model", args.model,
            "--title", f"{args.task} | {args.run_id}", "--format", "json"]
    if agent:
        base += ["--agent", agent]

    env = build_env(workspace, args.doc_parser)
    session_path = results_dir / "opencode_session.jsonl"
    log_path = results_dir / "opencode_stderr.log"

    print(f"Runner:    opencode ({oc_pkg})")
    print(f"Model:     {args.model}")
    joined_skills = ", ".join(skill_names)
    print(f"Prompt:    {args.prompt_mode} mode, skills {args.skill_mode} ({joined_skills})")
    print(f"Harness:   {components['mode']}, {components['always_on_chars']:,} always-on chars"
          + (f", skills {', '.join(rendered.harness_skills)}" if rendered.harness_skills else "")
          + (f", subagents {', '.join(rendered.subagents)}" if rendered.subagents else "")
          + (f", scripts {', '.join(rendered.scripts)}" if rendered.scripts else "")
          + (f", plugins {', '.join(rendered.plugins)}" if rendered.plugins else ""))
    allowed = (", ".join(sorted(k for k, v in tools.items() if v)) if tools
               else "OpenCode default set")
    print(f"Tools:     {args.tool_mode} ({allowed})")
    print(f"Workspace: {workspace}")
    print(f"Output:    {output_dir}")
    print()

    t0 = time.time()
    rc = invoke([build_prompt(task, workspace)], base=base, env=env, timeout=args.timeout,
                session_path=session_path, log_path=log_path, append=False)

    summary = oc_session.parse(session_path)
    for _ in range(args.max_continue):
        remaining = args.timeout - (time.time() - t0)
        if remaining < 60 or not summary["session_id"]:
            break
        if (any(f.is_file() for f in output_dir.rglob("*"))
                and summary["final_text"].strip() == "DONE"):
            break
        before = len(summary["tool_calls"])
        rc = invoke(["--session", summary["session_id"], NUDGE], base=base, env=env,
                    timeout=remaining, session_path=session_path, log_path=log_path, append=True)
        summary = oc_session.parse(session_path)
        if len(summary["tool_calls"]) == before:
            break

    wall = time.time() - t0

    graded_output = results_dir / "output"
    graded_output.mkdir(parents=True, exist_ok=True)
    for src in output_dir.rglob("*"):
        if src.is_file():
            dest = graded_output / src.relative_to(output_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    oc_session.to_transcript(session_path, results_dir / "transcript.jsonl")
    docs_read, docs_total = oc_session.doc_coverage(summary, docs_dest)

    metrics = {
        "runner": "opencode",
        "model": args.model,
        "task": args.task,
        "run_id": args.run_id,
        "turn_count": summary["steps"],
        "input_tokens": summary["input_tokens"],
        "output_tokens": summary["output_tokens"],
        "reasoning_tokens": summary["reasoning_tokens"],
        "total_tokens": summary["input_tokens"] + summary["output_tokens"],
        "wall_clock_seconds": round(wall, 1),
        "cost_usd": round(summary["cost"], 4),
        "finished_cleanly": rc == 0,
        "exit_code": rc,
        "tool_calls": len(summary["tool_calls"]),
        "documents_read": docs_read,
        "total_documents": docs_total,
        "harness_components": components,
        "harness_component_use": component_use(summary, rendered),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    (results_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    delivered = sorted(p.name for p in graded_output.rglob("*") if p.is_file())

    if not args.keep_node_modules:
        # Nothing downstream reads the per-run node_modules, and it exhausts inodes.
        shutil.rmtree(workspace / ".opencode" / "node_modules", ignore_errors=True)

    print()
    print("=" * 60)
    print(f"Run complete: {args.run_id}")
    print(f"  Steps:          {metrics['turn_count']}")
    print(f"  Tool calls:     {metrics['tool_calls']}")
    print(f"  Input tokens:   {metrics['input_tokens']:,}")
    print(f"  Output tokens:  {metrics['output_tokens']:,}")
    print(f"  Wall clock:     {metrics['wall_clock_seconds']}s")
    print(f"  Docs read:      {docs_read}/{docs_total}")
    print(f"  Deliverables:   {', '.join(delivered) or 'NONE'}")
    print(f"  Exit code:      {rc}")
    print(f"\nResults saved to: {results_dir}")


parser = argparse.ArgumentParser(description="Run a LAB task through OpenCode")
parser.add_argument("--results-root", help="Directory for run artifacts")
parser.add_argument("--model", required=True,
                    help="OpenCode model id (default: %(default)s)")
parser.add_argument("--task", required=True,
                    help="Task ID (e.g., corporate-ma/review-data-room-red-flag-review)")
parser.add_argument("--run-id", default=None, help="Run identifier (auto-generated if omitted)")
parser.add_argument("--max-turns", type=int, default=200, help="OpenCode agent step cap")
parser.add_argument("--timeout", type=int, default=3600, help="Wall-clock budget in seconds")
parser.add_argument("--max-continue", type=int, default=2,
                    help="Continue-nudge resumes if the agent stops early")
parser.add_argument("--reasoning-effort", default=None,
                    help="thinkingLevel override (low/high). Omit to use OpenCode's "
                         "default, which is already 'high' for gemini-3 models.")
parser.add_argument("--keep-node-modules", action="store_true",
                    help="Keep the workspace's .opencode/node_modules after the run. Off by "
                         "default: it is regenerated per run, nothing downstream reads it, and "
                         "at sweep scale it exhausts filesystem inodes.")
parser.add_argument("--skills", nargs="*", default=None,
                    help="Skills to load (default: all in harness/skills)")
parser.add_argument("--prompt-mode", choices=["append", "replace"], default="append",
                    help="append: AGENTS.md on top of OpenCode's stock prompt (default). "
                         "replace: primary-agent body supplants it.")
parser.add_argument("--harness-file", default=None,
                    help="Replace the agent prompt body with this file's contents "
                         "(the AGENTS.md body under the default append mode). Skills, "
                         "scripts and tool gating are unaffected. Omit for the base "
                         "harness, which is what every baseline run uses.")
parser.add_argument("--harness-dir", default=None,
                    help="Render a whole harness component tree: systemprompt.md, "
                         "guardrails.md, LongTermMEMORY.md, tool_descriptions/, plus on-demand "
                         "skills/, sub_agents/, scripts/ and plugin/. A directory holding only "
                         "AGENTS.md is treated as a legacy single-body harness, so stored "
                         "library entries load unchanged. Mutually exclusive with "
                         "--harness-file.")
parser.add_argument("--allow-subagents", action="store_true",
                    help="Permit sub_agents/ in a --harness-dir tree, which allows OpenCode's "
                         "`task` tool. Off by default: the native harness has no subagent "
                         "equivalent, so such runs are not tool-matched to native baselines. A "
                         "tree containing sub_agents/ is refused rather than rendered without "
                         "them, so the component is never scored as inert when it was dropped.")
parser.add_argument("--skill-mode", choices=["inline", "native"], default="inline",
                    help="inline: skill manuals in the prompt, as harness.run does (default). "
                         "native: .opencode/skills/ loaded on demand.")
parser.add_argument("--tool-mode", choices=["base", "native"], default="base",
                    help="base: only the tools harness/tools.py exposes — bash, read, write, "
                         "edit, glob, grep (default). native: OpenCode's full set, which adds "
                         "websearch, webfetch, codesearch, task subagents, todowrite, lsp.")
parser.add_argument("--extra-tools", nargs="*", default=[], metavar="TOOL",
                    help="Extra OpenCode tools to allow on top of --tool-mode base, e.g. "
                         "'task' for subagents. Empty by default: the base gate is tool-matched "
                         "to the native harness, and anything added here breaks that match, so "
                         "such runs are not comparable to native baselines.")
parser.add_argument("--doc-parser", choices=["sandbox", "host"], default="sandbox",
                    help="Where scripts/read_doc.py parses binary documents (default: %(default)s)")
parser.add_argument("--opencode-dir", default=DEFAULT_OPENCODE_DIR, help="OpenCode checkout root")
parser.add_argument("--bun", default=DEFAULT_BUN, help="Path to the bun binary")


if __name__ == "__main__":
    main(parser.parse_args())
