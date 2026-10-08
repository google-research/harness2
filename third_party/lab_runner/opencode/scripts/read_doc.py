#!/usr/bin/env python3
"""Extract text from a benchmark document (.docx/.pdf/.pptx/.xlsx).

Stands in for the native harness's `read` tool and shares its parser,
sandbox/parsers/parse_doc.py; `--in sandbox` runs it in a one-shot container so document
bytes are never parsed by host Python, `--in host` imports it in-process.

Usage:
    python3 scripts/read_doc.py <path> [--in {sandbox,host}]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PARSEABLE = ("docx", "pdf", "pptx", "xlsx")
DEFAULT_IMAGE = os.environ.get("LAB_SANDBOX_IMAGE", "lab-sandbox:latest")


def _podman_env() -> dict:
    """Environment for podman, with XDG_DATA_HOME removed.

    Rootless podman roots its image store there, and the runner repoints it per run.
    """
    env = os.environ.copy()
    env.pop("XDG_DATA_HOME", None)
    return env


def parse_in_sandbox(path: Path, ext: str) -> int:
    """Run `parse-doc` inside a throwaway container with the file mounted read-only."""
    result = subprocess.run(
        [
            "podman", "run", "--rm",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "-v", f"{path}:/doc/{path.name}:ro",
            DEFAULT_IMAGE,
            "parse-doc", ext, f"/doc/{path.name}",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        env=_podman_env(),
    )
    if result.returncode != 0:
        # Drop podman's cgroup/systemd chatter so a real parse error is legible.
        noise = [ln for ln in result.stderr.splitlines() if 'level=warning' not in ln]
        print("\n".join(noise).strip() or "parse failed", file=sys.stderr)
        return 1
    sys.stdout.write(result.stdout)
    return 0


def _find_parsers_dir() -> Path | None:
    """Locate sandbox/parsers/ via LAB_BENCH_ROOT, else by walking up from this file."""
    root = os.environ.get("LAB_BENCH_ROOT")
    if root and (d := Path(root) / "sandbox" / "parsers").is_dir():
        return d
    for parent in Path(__file__).resolve().parents:
        if (d := parent / "sandbox" / "parsers" / "parse_doc.py").is_file():
            return d.parent
    return None


def parse_on_host(path: Path, ext: str) -> int:
    """Import the same parser module and run it in-process."""
    parsers = _find_parsers_dir()
    if parsers is None:
        print("cannot locate sandbox/parsers/parse_doc.py; set LAB_BENCH_ROOT", file=sys.stderr)
        return 1
    sys.path.insert(0, str(parsers))
    import parse_doc  # type: ignore[import-not-found]

    try:
        sys.stdout.write(parse_doc.PARSERS[ext](str(path)))
    except Exception as e:  # noqa: BLE001
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path")
    ap.add_argument("--in", dest="where", default=os.environ.get("LAB_DOC_PARSER", "sandbox"),
                    choices=["sandbox", "host"])
    args = ap.parse_args()

    path = Path(args.path).resolve()
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 1

    ext = path.suffix.lower().lstrip(".")
    if ext not in PARSEABLE:
        sys.stdout.write(path.read_text(encoding="utf-8", errors="replace"))
        return 0

    return parse_in_sandbox(path, ext) if args.where == "sandbox" else parse_on_host(path, ext)


if __name__ == "__main__":
    sys.exit(main())
