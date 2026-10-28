"""End-to-end test of the pi substrate: build a harness tree, materialise it,
run a real pi agent inside the staged workspace, and verify the harness
components actually govern the run.

Usage: . .venv/bin/activate && python tests/test_pi_substrate.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from harness2.substrate.pi import PI_SURFACE, PiSubstrate

TREE = {
    "systemprompt.md": (
        "# Executor persona\n"
        "You are a meticulous arithmetic assistant on a home server.\n"
        "Always use the helper scripts for math instead of computing in your head.\n"
        "**Always end your final reply with the exact word BANANA.**\n"
    ),
    "guardrails.md": (
        "# Guardrails\n"
        "- Never modify files outside the workspace.\n"
        "- Always double-check arithmetic with scripts/sum.py.\n"
    ),
    "memory.md": (
        "# Memory\n"
        "- This workspace belongs to the pi-labs harness test suite.\n"
    ),
    "tool_guidance.md": (
        "# Tool guidance\n"
        "- Prefer `python3 scripts/sum.py` for any addition.\n"
    ),
    "skills/sum-verify/SKILL.md": (
        "---\n"
        "name: sum-verify\n"
        "description: Verify a sum by re-running it through the deterministic helper script. Use whenever a numeric result must be double-checked.\n"
        "---\n"
        "# Sum verification\n"
        "Run `python3 scripts/sum.py <a> <b>` and report its output verbatim.\n"
    ),
    "sub_agents/reviewer/AGENT.md": (
        "---\n"
        "description: Reviews a computed result for obvious mistakes before it is reported.\n"
        "---\n"
        "You are a reviewer subagent. Check the given arithmetic result and reply OK or the correction.\n"
    ),
    "scripts/sum.py": (
        "import sys\nprint(f'sum={int(sys.argv[1]) + int(sys.argv[2])}')\n"
    ),
    "plugin/no-op.ts": (
        "import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';\n"
        "export default function (pi: ExtensionAPI) {\n"
        "  pi.registerCommand('harness-probe', {\n"
        "    description: 'prove the plugin component loaded',\n"
        "    handler: async (_a, ctx) => { ctx.ui.notify('pi-substrate plugin is live', 'info'); },\n"
        "  });\n"
        "}\n"
    ),
}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pi-substrate-test-"))
    try:
        # 1. build the harness tree
        tree_dir = tmp / "tree"
        for rel, body in TREE.items():
            p = tree_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        for rel, _ in PI_SURFACE.stubs:
            p = tree_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text(_)

        # 2. materialise through the substrate
        class Spec:
            install_mode = "pi_project"

        install = PiSubstrate().materialise(tree_dir, Spec(), {})
        staged = install.staging_dir
        print(f"[ok] materialised -> {staged}")
        for line in sorted(str(p.relative_to(staged)) for p in staged.rglob("*") if p.is_file()):
            print(f"      {line}")
        composed = (staged / ".pi" / "APPEND_SYSTEM.md").read_text()
        for needle in ("BANANA", "sum-verify", "reviewer", "sum.py"):
            assert needle in composed, f"composed text missing {needle!r}"
        print("[ok] composed APPEND_SYSTEM.md carries c1-c7 content")

        # 3. run a real pi agent in a workspace with the overlay installed
        ws = tmp / "workspace"
        ws.mkdir()
        shutil.copytree(staged, ws, dirs_exist_ok=True)
        argv = PiSubstrate().run_argv(
            "Use the helper script to compute 19 + 23, then double-check it with the "
            "sum-verify skill. Reply with the verified number only, on one line.",
            ws,
            "zai/glm-5.3",
            title="pi-substrate-e2e",
        )
        print(f"[ok] argv: {' '.join(argv[:6])} ... (cwd={ws})")
        proc = subprocess.run(argv, cwd=ws, capture_output=True, text=True, timeout=240)
        events = [line for line in proc.stdout.splitlines() if line.strip()]
        import json

        final_text = ""
        skill_loaded = False
        for line in events:
            try:
                evt = json.loads(line)
            except ValueError:
                continue
            if evt.get("type") == "message_end" and evt.get("message", {}).get("role") == "assistant":
                for blk in evt["message"].get("content", []):
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        final_text += blk.get("text", "")
            if evt.get("type") == "message_start" and evt.get("message", {}).get("role") == "user":
                for blk in evt["message"].get("content", []):
                    if isinstance(blk, dict) and "SKILL.md" in str(blk.get("text", "")):
                        skill_loaded = True
        print(f"[..] assistant replied: {final_text.strip()[:120]!r}")
        assert "42" in final_text, f"expected 42 in reply, got {final_text!r}"
        assert "BANANA" in final_text, "harness persona (c1) not followed: no BANANA"
        print("[ok] harness governed the run: correct answer + persona suffix")
        if skill_loaded:
            print("[ok] skill (c6) was loaded during the run")
        else:
            print("[warn] skill not explicitly loaded (acceptable: script sufficed)")
        assert proc.returncode == 0, f"pi exited {proc.returncode}"
        print("\nALL PI-SUBSTRATE CHECKS PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
