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

"""AntigravitySubstrate: Google Antigravity CLI (`agy`) as a solver substrate.

Carries the c1..c8 surface (ANTIGRAVITY_SURFACE), the `agy_root` install mode
(GEMINI.md, .agents/{skills,hooks.json}, scripts/, sub_agents/), optional immutable
off-surface pinned policy staging with SHA-256 verification, the official headless
`agy -p --output-format stream-json --sandbox` CLI contract, and the NDJSON
stream-json trajectory renderer.
"""

from __future__ import annotations

import hashlib
import json
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

AGY_RULE_MAX_BYTES = 24_000

ANTIGRAVITY_SURFACE = SurfaceSpec(
    name="antigravity",
    always_on=("GEMINI.md",),
    always_on_max=AGY_RULE_MAX_BYTES,
    always_on_cap_reason=(
        "Antigravity caps each GEMINI.md rule file at 24,000 bytes (truncating on line "
        "boundaries when exceeded) and shares a 20,000-token aggregate rules budget"
    ),
    skill_dir=".agents/skills",
    skill_name_rule="must_match",
    trigger_hint=(
        'Fix it by adding a trigger line naming `{name}` to GEMINI.md, e.g. "When '
        "<the situation this skill handles>, read .agents/skills/{name}/SKILL.md "
        'and follow it."'
    ),
    autotrigger_file="GEMINI.md",
    trigger_line="- When {desc}, read .agents/skills/{name}/SKILL.md and follow it.\n",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file=".agents/hooks.json",
    hooks_schema="antigravity",
    always_on_cap_unit="bytes",
    hook_events=frozenset(
        {
            "SessionStart",
            "PreToolUse",
            "PostToolUse",
            "PreInvocation",
            "PostInvocation",
            "Stop",
        }
    ),
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
            "<!-- c5: optional delegated subagent briefs (harness2 convention; not auto-discovered "
            "by native `agy` without an explicit trigger in GEMINI.md). Add one as "
            "sub_agents/<name>/AGENT.md with frontmatter `description:`. -->\n",
        ),
        (
            ".agents/README.md",
            "<!-- c8: Antigravity lifecycle hooks -- the only lever that acts INSIDE the "
            "tool/turn loop so the model cannot skip it. Add ONE file, .agents/hooks.json, "
            'named-hook schema: {"<hook-name>": {"PostToolUse": [{"matcher": '
            '"write_file|edit_file", "hooks": [{"type": "command", "command": "..."}]}], '
            '"Stop": [{"type": "command", "command": "..."}]}}. '
            "NOTE: PreToolUse/PostToolUse use grouped {matcher, hooks} arrays; "
            "SessionStart/PreInvocation/PostInvocation/Stop use flat handler arrays. "
            "In Stop hooks, always re-verify on continuation turns and cap `continue` responses "
            "at `executionNum >= 2` (respecting `continuationActive`) to prevent infinite loops. -->\n",
        ),
        (
            ".agents/skills/README.md",
            "<!-- c6: reusable workflows. Add one as .agents/skills/<name>/SKILL.md with YAML "
            "frontmatter: BOTH `name:` (matching the directory) and `description:` are REQUIRED. "
            "Name every skill in GEMINI.md so it is triggered on demand. -->\n",
        ),
    ),
)

AGY_ROOT_ENTRIES = ("GEMINI.md", ".agents", "scripts", "sub_agents")
PINNED_POLICY_BANNER = (
    "<!-- HARNESS2 PINNED IMMUTABLE POLICY START (DO NOT EDIT OR REMOVE) -->\n"
)
PINNED_POLICY_END = (
    "\n<!-- HARNESS2 PINNED IMMUTABLE POLICY END -->\n\n"
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_pinned_policy(staged_dir: Path, expected_sha256: str) -> None:
    """Fail closed if staged GEMINI.md lacks an unaltered leading pinned immutable policy."""
    if not expected_sha256:
        return
    gemini_md = Path(staged_dir) / "GEMINI.md"
    if not gemini_md.is_file():
        raise ValueError("staged GEMINI.md is missing; pinned policy integrity failed")
    text = gemini_md.read_text(encoding="utf-8", errors="replace")
    if not text.startswith(PINNED_POLICY_BANNER):
        raise ValueError("pinned policy must appear at the very start of staged GEMINI.md")
    if PINNED_POLICY_END not in text:
        raise ValueError("pinned policy end marker missing from staged GEMINI.md")
    if text.count(PINNED_POLICY_BANNER) != 1 or text.count(PINNED_POLICY_END) != 1:
        raise ValueError("duplicate pinned policy markers detected in staged GEMINI.md")
    start = len(PINNED_POLICY_BANNER)
    end = text.index(PINNED_POLICY_END, start)
    actual = _sha256_bytes(text[start:end].encode("utf-8"))
    if actual != expected_sha256:
        raise ValueError(
            f"pinned policy SHA-256 mismatch: expected {expected_sha256[:12]}, got {actual[:12]}"
        )


class AntigravitySubstrate:
    """Official Google Antigravity CLI (`agy`) behind the shared Substrate interface."""

    name = "agy"

    def __init__(
        self,
        launcher: Launcher | None = None,
        *,
        pinned_policy: Path | str | None = None,
        sandbox: bool | None = None,
        cli_bin: str | None = None,
    ):
        self._launcher = launcher
        self._pinned_policy = Path(pinned_policy) if pinned_policy is not None else None
        self._sandbox = config.AGY_SANDBOX if sandbox is None else bool(sandbox)
        self._cli_bin = cli_bin

    def surface(self) -> SurfaceSpec:
        return ANTIGRAVITY_SURFACE

    def _resolve_cli(self) -> str:
        if self._cli_bin:
            return self._cli_bin
        return shutil.which("agy") or "agy"

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage the `agy_root` entries and optionally prepend a read-only pinned policy."""
        if spec.install_mode != "agy_root":
            raise ValueError(
                f"install_mode {spec.install_mode!r} is not the antigravity mode; this substrate "
                f"supports exactly one: 'agy_root'"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(ANTIGRAVITY_SURFACE, tree_dir):
            raise ValueError(f"{tree_dir} is not an antigravity harness tree (no GEMINI.md)")

        staged = Path(tempfile.mkdtemp(prefix="adapt-agy-root-"))
        pinned_sha = ""
        try:
            for entry in AGY_ROOT_ENTRIES:
                src = tree_dir / entry
                if not src.exists():
                    continue
                if src.is_dir():
                    shutil.copytree(src, staged / entry)
                else:
                    shutil.copy2(src, staged / entry)

            staged_resolved = staged.resolve()
            for key, body in ctx.items():
                if key.startswith("file:"):
                    rel = HT.normalize_rel(key[len("file:") :])
                    dest = (staged / rel).resolve()
                    if not dest.is_relative_to(staged_resolved):
                        raise ValueError(f"ctx file escapes staging_dir: {rel!r}")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(body, encoding="utf-8")

            gemini_path = staged / "GEMINI.md"
            policy_path = self._pinned_policy
            if policy_path is None and ctx.get("pinned_policy_file"):
                policy_path = Path(ctx["pinned_policy_file"])
            if policy_path is not None:
                policy_text = Path(policy_path).read_text(encoding="utf-8").strip()
                pinned_sha = _sha256_bytes(policy_text.encode("utf-8"))
                evolved = (
                    gemini_path.read_text(encoding="utf-8", errors="replace")
                    if gemini_path.is_file()
                    else ""
                )
                # Strip an existing leading pinned block to avoid double insertion on re-stage.
                if evolved.startswith(PINNED_POLICY_BANNER) and PINNED_POLICY_END in evolved:
                    tail_idx = evolved.index(PINNED_POLICY_END) + len(PINNED_POLICY_END)
                    evolved = evolved[tail_idx:]
                combined = PINNED_POLICY_BANNER + policy_text + PINNED_POLICY_END + evolved
                combined = combined.replace("\r\n", "\n")
                combined_bytes = len(combined.encode("utf-8"))
                if combined_bytes > AGY_RULE_MAX_BYTES:
                    raise ValueError(
                        f"combined pinned policy + evolved GEMINI.md is {combined_bytes} bytes, "
                        f"exceeding Antigravity's {AGY_RULE_MAX_BYTES}-byte per-file limit"
                    )
                with gemini_path.open("w", encoding="utf-8", newline="\n") as fh:
                    fh.write(combined)
                verify_pinned_policy(staged, pinned_sha)
            elif gemini_path.is_file():
                raw_bytes = len(gemini_path.read_bytes())
                if raw_bytes > AGY_RULE_MAX_BYTES:
                    raise ValueError(
                        f"staged GEMINI.md is {raw_bytes} bytes, exceeding Antigravity's "
                        f"{AGY_RULE_MAX_BYTES}-byte per-file limit"
                    )
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise

        extras = {
            "install_mode": "agy_root",
            **{k: v for k, v in ctx.items() if not k.startswith("file:")},
        }
        fp = RM.fingerprint(tree_dir)
        if pinned_sha:
            extras["pinned_policy_sha256"] = pinned_sha
            fp = _sha256_bytes(f"{fp}:{pinned_sha}".encode("utf-8"))[:16]
        return Install(
            staging_dir=staged,
            fingerprint=fp,
            extras=extras,
        )

    def exec_argv(
        self,
        prompt: str,
        workspace: Path,
        model: str,
        *,
        effort: str = "",
        timeout: int | None = None,
        sandbox: bool | None = None,
        skip_permissions: bool = True,
        disable_slash_commands: bool = False,
        settings: Path | str | None = None,
    ) -> list[str]:
        """Official headless `agy -p` argv with NDJSON stream output and optional sandbox."""
        use_sandbox = self._sandbox if sandbox is None else bool(sandbox)
        argv = [
            self._resolve_cli(),
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--add-dir",
            str(workspace),
        ]
        if effort:
            argv += ["--effort", effort]
        if use_sandbox:
            argv.append("--sandbox")
        if skip_permissions:
            argv.append("--dangerously-skip-permissions")
        if disable_slash_commands:
            argv.append("--disable-slash-commands")
        if timeout is not None and int(timeout) > 0:
            argv += ["--print-timeout", f"{int(timeout)}s"]
        if settings is not None:
            argv += ["--settings", str(settings)]
        return argv

    def exec_env(
        self,
        base_env: Mapping[str, str] | None = None,
        *,
        keep: tuple[str, ...] = (),
    ) -> dict[str, str]:
        """Scrub host secrets before launching `agy`."""
        return config.scrub_env(base_env, keep=keep)

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if install.extras.get("pinned_policy_sha256"):
            verify_pinned_policy(install.staging_dir, install.extras["pinned_policy_sha256"])
        if self._launcher is None:
            raise RuntimeError(
                "AntigravitySubstrate was constructed without a launcher body; the benchmark "
                "adapter must supply one"
            )
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000
    ) -> str:
        return render_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self) -> None:
        """Verify the `agy` CLI binary is installed and responds to `--help`; spends no tokens."""
        binary = shutil.which(self._resolve_cli())
        if not binary:
            raise RuntimeError(
                "Antigravity CLI (`agy`) not found on PATH; install Google Antigravity CLI "
                "(https://antigravity.google/docs) before using AntigravitySubstrate"
            )
        res = subprocess.run(
            [binary, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"Antigravity CLI (`{binary} --help`) exited with code {res.returncode}: "
                f"{(res.stderr or res.stdout)[:200]}"
            )


_TRUNC_FMT = "…(+{n} chars — render truncation)"


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\x00", "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:n] + _TRUNC_FMT.format(n=len(s) - n)


def parse_stream(path: Path) -> list[dict]:
    """Parse `agy -p --output-format stream-json` NDJSON lines tolerantly."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{"event": "_read_error", "error": str(exc)}]
    events: list[dict] = []
    for ln in raw.replace("\x00", "").splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000) -> str:
    """Render Antigravity's `--output-format stream-json` NDJSON into the shared grammar.

    Verified against live `agy -p --output-format stream-json` events:
      - `init`: model/cwd/permission_mode metadata
      - `step_update`: `step_index`, `state` (ACTIVE|DONE|ERROR), `step_type`
        (`user_input`, `agent_response` with `text_delta`/`thinking_delta`/`usage`,
        `tool` with `tool_name` and `tool_info.{parameters,output,error}`)
      - `result`: terminal `status` (SUCCESS|ERROR), `response`, `error`, `usage`
    """
    events = parse_stream(Path(path))
    if events and events[0].get("event") == "_read_error":
        return f"(failed to read trajectory: {events[0].get('error')})"

    step_order: list[int] = []
    steps: dict[int, dict] = {}
    init_meta: dict | None = None
    result_obj: dict | None = None

    for ev in events:
        etype = ev.get("event")
        if etype == "init" and isinstance(ev.get("init"), dict):
            init_meta = ev["init"]
            continue
        if etype == "result" and isinstance(ev.get("result"), dict):
            result_obj = ev["result"]
            continue
        if etype != "step_update":
            continue
        su = ev.get("step_update")
        if not isinstance(su, dict):
            continue
        idx = su.get("step_index")
        if idx is None:
            continue
        if idx not in steps:
            step_order.append(idx)
            steps[idx] = {
                "step_type": su.get("step_type", ""),
                "state": su.get("state", ""),
                "text_parts": [],
                "thinking_parts": [],
                "tool_name": "",
                "tool_info": {},
                "usage": None,
                "error": "",
            }
        rec = steps[idx]
        if su.get("step_type"):
            rec["step_type"] = su["step_type"]
        if su.get("state"):
            rec["state"] = su["state"]
        if isinstance(su.get("text_delta"), str):
            rec["text_parts"].append(su["text_delta"])
        if isinstance(su.get("thinking_delta"), str):
            rec["thinking_parts"].append(su["thinking_delta"])
        if su.get("tool_name"):
            rec["tool_name"] = str(su["tool_name"])
        if isinstance(su.get("tool_info"), dict):
            rec["tool_info"] = {**rec["tool_info"], **su["tool_info"]}
        if isinstance(su.get("usage"), dict):
            rec["usage"] = su["usage"]
        if su.get("error"):
            rec["error"] = str(su["error"])

    lines: list[str] = []
    if init_meta:
        lines.append(
            f"[HARNESS] agy init | model={init_meta.get('model', '')} "
            f"permission_mode={init_meta.get('permission_mode', '')}"
        )

    for idx in step_order:
        rec = steps[idx]
        stype = rec["step_type"]
        if stype == "tool":
            info = rec["tool_info"] or {}
            name = rec["tool_name"] or info.get("name") or "tool"
            params = info.get("parameters")
            args = (
                json.dumps(params, default=str, ensure_ascii=False)
                if params is not None
                else ""
            )
            lines.append(f"[ACTION] {name}({_clip(args, int(args_trunc))})")
            err = info.get("error") or rec["error"]
            out = info.get("output")
            if err or rec["state"] == "ERROR":
                lines.append(f"[OBSERVATION ERROR] {_clip(err or out, obs_trunc)}")
            elif out is not None:
                lines.append(f"[OBSERVATION] {_clip(out, obs_trunc)}")
            elif rec["state"] == "ACTIVE":
                lines.append("[OBSERVATION] (command still ACTIVE at end of stream)")
        elif stype == "agent_response":
            thinking = "".join(rec["thinking_parts"]).strip()
            if thinking:
                lines.append(f"[REASONING] {_clip(thinking, obs_trunc)}")
            text = "".join(rec["text_parts"]).strip()
            if text:
                lines.append(f"[ASSISTANT] {_clip(text, obs_trunc)}")
            if rec["usage"]:
                u = rec["usage"]
                lines.append(
                    "[HARNESS] turn usage | tokens in={i} cached={c} out={o} thinking={t}".format(
                        i=u.get("input_tokens"),
                        c=u.get("cache_read_tokens"),
                        o=u.get("output_tokens"),
                        t=u.get("thinking_tokens"),
                    )
                )
        elif stype == "user_input":
            continue
        elif rec["error"]:
            lines.append(f"[ERROR] {_clip(rec['error'], 1000)}")

    if result_obj:
        status = str(result_obj.get("status") or "")
        if status == "ERROR" or result_obj.get("error"):
            lines.append(f"[ERROR] {_clip(result_obj.get('error') or status, 1000)}")
        else:
            resp = str(result_obj.get("response") or "").strip()
            has_assistant = any(ln.startswith("[ASSISTANT]") for ln in lines)
            if resp and not has_assistant:
                lines.append(f"[ASSISTANT] {_clip(resp, obs_trunc)}")
            u = result_obj.get("usage")
            if isinstance(u, dict):
                lines.append(
                    "[HARNESS] result | status={s} turns={n} total_tokens={tot}".format(
                        s=status,
                        n=result_obj.get("num_turns"),
                        tot=u.get("total_tokens"),
                    )
                )

    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered


def final_message(path: Path) -> str:
    """Return the final response text from an `agy --output-format stream-json` stream."""
    last_step_idx: int | None = None
    step_deltas: dict[int, list[str]] = {}
    fallback_deltas: list[str] = []
    for ev in parse_stream(Path(path)):
        etype = ev.get("event")
        if etype == "result" and isinstance(ev.get("result"), dict):
            resp = ev["result"].get("response")
            if isinstance(resp, str) and resp.strip():
                return resp
        elif etype == "step_update" and isinstance(ev.get("step_update"), dict):
            su = ev["step_update"]
            if su.get("step_type") == "agent_response" and isinstance(su.get("text_delta"), str):
                idx = su.get("step_index")
                if isinstance(idx, int):
                    last_step_idx = idx
                    step_deltas.setdefault(idx, []).append(su["text_delta"])
                else:
                    fallback_deltas.append(su["text_delta"])
    if last_step_idx is not None:
        return "".join(step_deltas[last_step_idx]).strip()
    return "".join(fallback_deltas).strip()


def stream_usage(path: Path) -> dict:
    """Extract terminal token usage & status metadata from an `agy` stream-json file."""
    for ev in reversed(parse_stream(Path(path))):
        if ev.get("event") == "result" and isinstance(ev.get("result"), dict):
            res = ev["result"]
            usage = res.get("usage") if isinstance(res.get("usage"), dict) else {}
            return {
                "status": str(res.get("status") or ""),
                "error": str(res.get("error") or ""),
                "duration_seconds": float(res.get("duration_seconds") or 0.0),
                "num_turns": int(res.get("num_turns") or 0),
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "thinking_tokens": int(usage.get("thinking_tokens") or 0),
                "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            }
    return {}
