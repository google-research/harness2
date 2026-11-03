"""Native subscription CLIs, with a new process and conversation for every call.

Credentials stay in the CLI's existing auth store. No proxy or API fallback is used.
The raw streams are retained separately from the method's ordinary audit records.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    blocked = {
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_FOUNDRY",
        "VERTEX_PROXY_KEY",
        "ANTHROPIC_VERTEX_PROJECT_ID",
    }
    return {
        key: value
        for key, value in {**os.environ, **(overrides or {})}.items()
        if key not in blocked and not key.endswith("API_KEY")
    }


def check_auth(channel: str) -> str:
    binary = "claude" if channel == "claude_cli" else "codex"
    if channel not in ("claude_cli", "codex_cli") or not shutil.which(binary):
        raise RuntimeError(f"Subscription channel unavailable: {channel}")
    cmd = [binary, "auth", "status"] if binary == "claude" else [binary, "login", "status"]
    p = subprocess.run(cmd, env=environment(), capture_output=True, text=True, timeout=30)
    if binary == "claude":
        status = json.loads(p.stdout) if p.returncode == 0 else {}
        ok = status.get("loggedIn") and status.get("authMethod") == "claude.ai"
        ok = ok and status.get("apiProvider") == "firstParty"
    else:
        ok = p.returncode == 0 and "ChatGPT" in p.stdout + p.stderr
    if not ok:
        raise RuntimeError(
            f"{binary} needs its native subscription login; API fallback is disabled"
        )
    return subprocess.check_output([binary, "--version"], text=True, timeout=30).strip()


def claude_result(stdout: str) -> dict:
    """Claude's JSON mode can be a result object or a verbose array of events."""
    value = json.loads(stdout)
    if isinstance(value, list):
        value = next((v for v in reversed(value) if v.get("type") == "result"), {})
    if not isinstance(value, dict) or value.get("is_error"):
        raise RuntimeError("Claude returned an error instead of a successful result")
    if value.get("subtype") != "success" or not value.get("result", "").strip():
        raise RuntimeError(f"Claude did not complete: {value.get('subtype')}")
    return value


def codex_result(stdout: str) -> dict:
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    if any(e.get("type") == "turn.failed" for e in events):
        raise RuntimeError("Codex turn failed; see the captured stream")
    completed = [e for e in events if e.get("type") == "turn.completed"]
    if not completed:
        raise RuntimeError("Codex stream has no completed turn")
    return {
        "result": "\n".join(
            e["item"].get("text", "")
            for e in events
            if e.get("type") == "item.completed"
            and e.get("item", {}).get("type") == "agent_message"
        ),
        "session_id": next(
            (e.get("thread_id") for e in events if e.get("type") == "thread.started"), None
        ),
        "usage": completed[-1].get("usage", {}),
    }


def invoke(
    channel: str,
    model: str,
    prompt: str,
    *,
    workspace: Path,
    audit: Path,
    effort: str = "high",
    timeout: int = 3600,
    edit: bool = True,
    extra_env: dict[str, str] | None = None,
    read_roots: tuple[Path, ...] = (),
) -> dict:
    audit.mkdir(parents=True, exist_ok=True)
    runtime = workspace / ".harness2-runtime"
    for sub in ("tmp", "cache"):
        (runtime / sub).mkdir(parents=True, exist_ok=True)
    child_env = environment(
        {
            **(extra_env or {}),
            "TMPDIR": str(runtime / "tmp"),
            "TMP": str(runtime / "tmp"),
            "TEMP": str(runtime / "tmp"),
            "XDG_CACHE_HOME": str(runtime / "cache"),
        }
    )
    if channel == "claude_cli":
        permitted = "Bash,Read,Write,Edit,Glob,Grep" if edit else ""
        cmd = [
            "claude",
            "-p",
            "--model",
            model,
            "--effort",
            effort,
            "--output-format",
            "json",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--tools",
            permitted,
        ]
        if edit:
            cmd += ["--permission-mode", "acceptEdits", "--allowedTools", permitted]
    elif channel == "codex_cli":
        filesystem = {
            ":root": "deny",
            ":minimal": "read",
            ":slash_tmp": "deny",
            str(Path(tempfile.gettempdir()).resolve()): "deny",
            str(runtime.resolve()): "write",
        }
        if not edit:
            filesystem[str(workspace.resolve())] = "read"
        for root in (Path(sys.base_prefix), Path("/opt/homebrew"), Path("/usr/local"), *read_roots):
            if root.exists():
                filesystem[str(root.resolve())] = "read"
        fs_toml = (
            "{" + ",".join(json.dumps(k) + "=" + json.dumps(v) for k, v in filesystem.items()) + "}"
        )
        cmd = [
            "codex",
            "exec",
            "--ignore-user-config",
            "--model",
            model,
            "-c",
            'model_provider="openai"',
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            'approval_policy="never"',
            "-c",
            "allow_login_shell=false",
            "--skip-git-repo-check",
            "--ephemeral",
            "-c",
            'default_permissions="harness2"',
            "-c",
            f'permissions.harness2.extends=":{"workspace" if edit else "read-only"}"',
            "-c",
            "permissions.harness2.filesystem=" + fs_toml,
            "--json",
            "-",
        ]
        if edit:
            cmd += ["--dangerously-bypass-hook-trust"]
    else:
        raise ValueError(f"Not a subscription CLI: {channel}")
    started = time.monotonic()
    with (audit / "stdout.jsonl").open("w") as out, (audit / "stderr.log").open("w") as err:
        p = subprocess.Popen(
            cmd,
            cwd=workspace,
            env=child_env,
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
            text=True,
            start_new_session=True,
        )
        try:
            p.communicate(prompt, timeout=timeout)
        except BaseException:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
            raise
    if p.returncode:
        raise RuntimeError(f"{channel} exited {p.returncode}; see {audit / 'stderr.log'}")
    stdout = (audit / "stdout.jsonl").read_text()
    result = claude_result(stdout) if channel == "claude_cli" else codex_result(stdout)
    result.update(
        channel=channel,
        requested_model=model,
        transport="subscription",
        wall_s=round(time.monotonic() - started, 2),
    )
    # CLI costUSD/total_cost_usd are list-price estimates, not subscription charges.
    (audit / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
