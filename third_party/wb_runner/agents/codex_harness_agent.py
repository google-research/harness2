"""CodexAgent subclass that installs the codex_root surface from WB_HARNESS_WS at the
workspace root and commits it pre-agent; an empty workspace reproduces stock codex."""
from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from workbuddy_bench.agents.codex_agent import CodexAgent

_HARNESS_ENTRIES = ("AGENTS.md", ".agents", "scripts", ".codex")


class CodexHarnessAgent(CodexAgent):
    """Codex CLI + a harness workspace installed (and committed) at /workspace."""

    def __init__(self, logs_dir: Path, *args, **kwargs):
        ws = kwargs.pop("WB_HARNESS_WS", None)
        self._harness_ws: Path | None = Path(ws) if ws else None
        super().__init__(logs_dir, *args, **kwargs)

    @staticmethod
    def name() -> str:
        return "codex-harness"

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._harness_ws is not None:
            await self._materialize_harness(environment)
        await super().run(instruction, environment, context)

    def _harness_payload(self) -> tuple[bytes, int]:
        """gzip tar of the codex_root entries present in the workspace (host side)."""
        ws = self._harness_ws
        payload = io.BytesIO()
        n = 0
        with tarfile.open(fileobj=payload, mode="w:gz") as tar:
            for entry in _HARNESS_ENTRIES:
                src = ws / entry
                if src.is_file():
                    tar.add(src, arcname=entry)
                    n += 1
                elif src.is_dir():
                    for p in sorted(src.rglob("*")):
                        if p.is_file():
                            tar.add(p, arcname=f"{entry}/{p.relative_to(src)}")
                            n += 1
        return payload.getvalue(), n

    async def _materialize_harness(self, environment: BaseEnvironment) -> None:
        """Install the harness at /workspace root and commit it pre-agent.

        The commit is conditional on staged changes, so a no-op harness is not a failure.
        """
        ws = self._harness_ws
        if ws is None or not ws.is_dir():
            return
        blob_bytes, n = self._harness_payload()
        if n == 0:
            return
        blob = base64.b64encode(blob_bytes).decode()
        cmd = (
            f"cd /workspace && "
            f"echo {blob} | base64 -d | tar -xzf - -C /workspace && "
            # Neutralize the install in the diff capture; skip when this is not a git repo.
            "if git rev-parse --git-dir >/dev/null 2>&1; then "
            "git add -A && "
            "{ git diff --cached --quiet || "
            "git -c user.email=harness@bench -c user.name=harness "
            'commit -q -m "harness install (pre-agent)"; }; '
            "fi"
        )
        await self.exec_as_agent(environment, command=cmd)
        log = getattr(self, "logger", None)
        if log is not None:
            log.info("materialized + committed %d harness file(s) from %s", n, ws)
