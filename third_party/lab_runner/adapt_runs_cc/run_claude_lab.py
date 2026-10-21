#!/usr/bin/env python3
"""Run one LAB task through Claude Code (`claude -p`), the Claude Code twin of
adapt_runs_cx/run_codex_lab.py.

Writes config.json, claude_session.jsonl, output/ and metrics.json last as the completion
record, so a dead model chain is never cached as a finished rollout. The `claude -p` argv
and the stream summary come from harness2.substrate.claude_code (HARNESS2_ROOT on the path),
so the LAB cell and the substrate can never disagree about the flag contract.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent
REPO = RUNNER_DIR.parents[1]
for p in (str(REPO), os.environ.get("HARNESS2_ROOT", "")):
    if p and p not in sys.path:
        sys.path.insert(0, p)

from external.tts_lab import config as C  # noqa: E402
from harness2.substrate.claude_code import ClaudeCodeSubstrate, summarize  # noqa: E402

SCRATCH_BASE = Path(os.environ.get("ADAPT_LABCC_SCRATCH") or (RUNNER_DIR / "scratch"))
RESULTS_ROOT = Path(os.environ.get("ADAPT_LABCC_RESULTS", str(RUNNER_DIR / "results")))
DEFAULT_MODEL = os.environ.get("ADAPT_LABCC_MODEL", "")

READ_DOC = REPO / "opencode" / "scripts" / "read_doc.py"

# The claude_root install, already in its runtime layout (the adapter materialised it).
HARNESS_ENTRIES = ("CLAUDE.md", ".claude", "scripts")


def _require_scratch(p: Path, what: str) -> Path:
    """Refuse to place any temp artifact outside the configured scratch root."""
    rp = Path(p).resolve()
    base = SCRATCH_BASE.resolve()
    if base != rp and base not in rp.parents:
        raise SystemExit(f"refusing to run: {what} resolves to {rp}, outside the scratch root "
                         f"{base} -- temp state must live under ADAPT_LABCC_SCRATCH")
    return rp


def scratch_paths(label: str, pid: int | None = None) -> tuple[Path, Path]:
    """(tmp_root, workspace) for one rollout, under the scratch root."""
    pid = os.getpid() if pid is None else pid
    tmp_root = _require_scratch(SCRATCH_BASE / "lab" / f"{label}__{pid}", "workspace root")
    return tmp_root, tmp_root / "ws"


def run_dir_for(run_id: str) -> Path:
    """<results_root>/<run_id>, with path escapes refused."""
    rid = str(run_id).strip()
    if not rid or rid.startswith("/") or ".." in rid.split("/"):
        raise SystemExit(f"bad --run-id {run_id!r}: must be a relative, escape-free id")
    root = Path(RESULTS_ROOT).resolve()
    if str(root).startswith("/tmp/"):
        raise SystemExit(f"refusing results root under /tmp: {root}")
    d = (root / rid).resolve()
    if root not in d.parents:
        raise SystemExit(f"run id escapes the results root: {run_id!r}")
    return d


def build_prompt(task_id: str, workspace: Path) -> str:
    """The assignment message: the instructions verbatim plus the concrete absolute paths."""
    instructions = str(C.task_json(task_id).get("instructions", "")).strip()
    return (
        f"=== WORKSPACE ===\n{workspace}\n\n"
        f"=== DOCUMENTS (read-only) ===\n{workspace / 'documents'}\n\n"
        f"=== OUTPUT DIRECTORY (deliverables go here) ===\n{workspace / 'output'}\n\n"
        f"=== ASSIGNMENT ===\n{instructions}"
    )


def install_harness(ws: Path, harness_dir: Path | None) -> list[str]:
    """Copy the installed harness into the workspace root; return the installed paths.

    The runtime's read_doc.py is installed last so no harness file can shadow it.
    """
    installed: list[str] = []
    if harness_dir is not None:
        hd = Path(harness_dir)
        for entry in HARNESS_ENTRIES:
            src = hd / entry
            if not src.exists():
                continue
            if src.is_file():
                shutil.copy2(src, ws / entry)
                installed.append(entry)
                continue
            for f in sorted(src.rglob("*")):
                if not f.is_file() or "__pycache__" in f.parts:
                    continue
                rel = f.relative_to(hd)
                dest = ws / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
                installed.append(str(rel))
    (ws / "scripts").mkdir(parents=True, exist_ok=True)
    if not READ_DOC.is_file():
        raise SystemExit(f"runtime document reader missing: {READ_DOC} -- without it every "
                         f"binary document 'read' silently returns noise")
    shutil.copy2(READ_DOC, ws / "scripts" / "read_doc.py")   # LAST: runtime wins
    return installed


def doc_coverage(docs_dir: Path, session_text: str) -> tuple[int, int]:
    """How many task documents the agent touched, by path substring in the raw session."""
    if not docs_dir.is_dir():
        return 0, 0
    rels = [str(p.relative_to(docs_dir)) for p in sorted(docs_dir.rglob("*")) if p.is_file()]
    read = sum(1 for r in rels if r in session_text)
    return read, len(rels)


def reap(hours: float) -> int:
    """Remove orphaned per-rollout scratch dirs older than `hours` (kill -9 leftovers)."""
    cutoff = time.time() - hours * 3600
    n = 0
    base = SCRATCH_BASE / "lab"
    if base.is_dir():
        for d in sorted(base.iterdir()):
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    _require_scratch(d, "reap target")
                    shutil.rmtree(d, ignore_errors=True)
                    n += 1
                    print(f"reaped {d}")
            except OSError:
                continue
    return n


def _run(cmd, *, cwd: Path, env: dict, stdout, stderr, timeout: int) -> int:
    """Run in its own process group; on timeout kill the group and return 124 like timeout(1)."""
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                            stdout=stdout, stderr=stderr, start_new_session=True)
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


def main(args) -> int:
    if args.reap_hours is not None:
        print(f"reaped {reap(args.reap_hours)} stale rollout dirs under {SCRATCH_BASE}")
        return 0
    if not args.task or not args.run_id:
        raise SystemExit("--task and --run-id are required (or use --reap-hours)")
    substrate = ClaudeCodeSubstrate()
    substrate.preflight()

    label = args.run_id.replace("/", "__")
    run_dir = run_dir_for(args.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp_root, ws = scratch_paths(label)

    def _die(signum, _frame):
        raise SystemExit(128 + signum)
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, _die)

    docs_src = C.documents_dir(args.task)
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.time()
    try:
        shutil.rmtree(tmp_root, ignore_errors=True)
        (ws / "output").mkdir(parents=True)
        if docs_src.is_dir():
            shutil.copytree(docs_src, ws / "documents")
            for p in (ws / "documents").rglob("*"):
                if p.is_file():
                    p.chmod(0o444)
        else:
            (ws / "documents").mkdir()
        harness_dir = Path(args.harness_dir).resolve() if args.harness_dir else None
        installed = install_harness(ws, harness_dir)

        cmd = substrate.exec_argv(
            build_prompt(args.task, ws), ws, args.model,
            effort=args.reasoning_effort, max_budget_usd=args.max_budget_usd,
        )
        version = subprocess.run([cmd[0], "--version"], capture_output=True, text=True,
                                 timeout=30).stdout.strip()
        config = {
            "runner": "claude_code",
            "claude_bin": cmd[0],
            "claude_version": version,
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "task": args.task,
            "run_id": args.run_id,
            "timeout": args.timeout,
            "harness_dir": str(harness_dir) if harness_dir else None,
            "harness_files": installed,
            "doc_parser": args.doc_parser,
            "workspace": str(ws),
            "started_at": started,
        }
        (run_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

        env = substrate.exec_env(os.environ)
        env.pop("XDG_DATA_HOME", None)       # read_doc.py sandbox mode needs podman's store
        env.update({"LAB_DOC_PARSER": args.doc_parser, "LAB_BENCH_ROOT": str(REPO)})
        session_path = run_dir / "claude_session.jsonl"
        with open(session_path, "w", encoding="utf-8") as out, \
             open(run_dir / "claude_stderr.log", "w", encoding="utf-8") as err:
            rc = _run(cmd, cwd=ws, env=env, stdout=out, stderr=err,
                      timeout=int(max(60, args.timeout)))
        wall = time.time() - t0

        graded = run_dir / "output"
        graded.mkdir(parents=True, exist_ok=True)
        for src in (ws / "output").rglob("*"):
            if src.is_file():
                dest = graded / src.relative_to(ws / "output")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        delivered = sorted(str(p.relative_to(graded)) for p in graded.rglob("*") if p.is_file())

        summary = summarize(session_path)
        if summary["n_events"] == 0:
            print(f"[run_claude_lab] NO parseable events in {session_path}; "
                  f"NOT writing metrics.json (rc={rc})", file=sys.stderr)
            return 2
        if rc == 0 and (summary["is_error"] or not delivered):
            rc = 1   # exit 0 with an error result or an empty output/ is a failure
        session_text = session_path.read_text(encoding="utf-8", errors="replace")
        docs_read, docs_total = doc_coverage(ws / "documents", session_text)
        scratch_left = sorted(str(p.relative_to(ws)) for p in ws.rglob("*")
                              if p.is_file() and not str(p.relative_to(ws)).startswith(
                                  ("documents/", "output/", "scripts/", ".claude/"))
                              and str(p.relative_to(ws)) != "CLAUDE.md")[:200]
        metrics = {
            "runner": "claude_code",
            "model": args.model,
            "task": args.task,
            "run_id": args.run_id,
            "turn_count": summary["turns"],
            "tool_calls": summary["tool_calls"],
            "result_subtype": summary["subtype"],
            "cost_usd": summary["cost_usd"],
            "input_tokens": summary["input_tokens"],
            "cache_read_input_tokens": summary["cache_read_input_tokens"],
            "cache_creation_input_tokens": summary["cache_creation_input_tokens"],
            "output_tokens": summary["output_tokens"],
            "total_tokens": summary["input_tokens"] + summary["output_tokens"],
            "wall_clock_seconds": round(wall, 1),
            "finished_cleanly": rc == 0,
            "exit_code": rc,
            "deliverables": delivered,
            "documents_read": docs_read,
            "total_documents": docs_total,
            "harness_dir": str(harness_dir) if harness_dir else None,
            "harness_files": installed,
            "workspace_scratch_files": scratch_left,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print(f"[run_claude_lab] {args.run_id}: rc={rc} turns={summary['turns']} "
              f"tools={summary['tool_calls']} wall={wall:.0f}s cost=${summary['cost_usd']:.2f} "
              f"deliverables={', '.join(delivered) or 'NONE'}")
        return rc
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


parser = argparse.ArgumentParser(description="Run one LAB task through Claude Code")
parser.add_argument("--task", default=None, help="Task ID (e.g. corporate-ma/<slug>)")
parser.add_argument("--run-id", default=None, help="Run identifier (namespaced, required)")
parser.add_argument("--model", default=DEFAULT_MODEL,
                    help="Claude model id or alias passed to `claude --model` "
                         "(default: %(default)s)")
parser.add_argument("--reasoning-effort", default="high",
                    help="`claude --effort` level (default: %(default)s)")
parser.add_argument("--max-budget-usd", default=os.environ.get("ADAPT_LABCC_MAX_BUDGET_USD", ""),
                    help="Optional `claude --max-budget-usd` cap per rollout")
parser.add_argument("--timeout", type=int, default=int(os.environ.get(
                    "ADAPT_LABCC_TIMEOUT", "3600")),
                    help="Wall-clock budget in seconds (default: %(default)s)")
parser.add_argument("--harness-dir", default=None,
                    help="Installed claude_root harness to copy to the workspace root "
                         "(CLAUDE.md, .claude/, scripts/). Omit for a bare workspace.")
parser.add_argument("--doc-parser", choices=["sandbox", "host"],
                    default=os.environ.get("LAB_DOC_PARSER", "sandbox"),
                    help="Where scripts/read_doc.py parses binary documents "
                         "(default: %(default)s)")
parser.add_argument("--reap-hours", type=float, default=None,
                    help="Instead of running a task: remove per-rollout scratch dirs under "
                         "$ADAPT_LABCC_SCRATCH/lab older than this many hours")

if __name__ == "__main__":
    sys.exit(main(parser.parse_args()))
