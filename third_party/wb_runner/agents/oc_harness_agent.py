"""OcAgent subclass that installs the on-demand harness components from WB_HARNESS_WS
into the agent's home; an empty workspace reproduces stock OpenCode."""
from __future__ import annotations

import base64
import io
import json
import tarfile
from pathlib import Path

from harbor.environments.base import BaseEnvironment

from workbuddy_bench.agents.oc_agent import OcAgent

_CONFIG_DIR = ".config/opencode"
_SKILL_SUBDIR = f"{_CONFIG_DIR}/skill"
_AGENT_SUBDIR = f"{_CONFIG_DIR}/agent"
_PLUGIN_SUBDIR = f"{_CONFIG_DIR}/plugin"
_SCRIPT_SUBDIR = "harness_scripts"


class OcHarnessAgent(OcAgent):
    """OpenCode + a mounted harness workspace."""

    def __init__(self, logs_dir: Path, *args, **kwargs):
        ws = kwargs.pop("WB_HARNESS_WS", None)
        self._harness_ws: Path | None = Path(ws) if ws else None
        super().__init__(logs_dir, *args, **kwargs)

    @staticmethod
    def name() -> str:
        return "oc-harness"

    async def _run_opencode(
        self, environment: BaseEnvironment, config_b64: str, *,
        model_arg: str, escaped_instruction: str,
    ) -> None:
        if self._harness_ws is not None:
            await self._materialize_harness(environment)
            config_b64 = self._inject_plugins(config_b64)
        await super()._run_opencode(
            environment, config_b64,
            model_arg=model_arg, escaped_instruction=escaped_instruction,
        )

    def _plugin_files(self) -> list[str]:
        ws = self._harness_ws
        root = ws / "plugin" if ws else None
        return sorted(p.name for p in root.glob("*.ts")) if root and root.is_dir() else []

    def _inject_plugins(self, config_b64: str) -> str:
        """Add the harness's plugins to the generated opencode.json.

        Specs are relative to the config file's own directory, which is how OpenCode
        resolves them; any plugin list already in the preset is preserved.
        """
        names = self._plugin_files()
        if not names:
            return config_b64
        cfg = json.loads(base64.b64decode(config_b64).decode())
        existing = list(cfg.get("plugin") or [])
        for n in names:
            spec = f"./plugin/{n}"
            if spec not in existing:
                existing.append(spec)
        cfg["plugin"] = existing
        return base64.b64encode(json.dumps(cfg).encode()).decode()

    async def _materialize_harness(self, environment: BaseEnvironment) -> None:
        """Install every on-demand component into the agent's home, as one base64 tar."""
        ws = self._harness_ws
        if ws is None or not ws.is_dir():
            return

        # Subagents flatten: the workspace stores sub_agents/<name>/AGENT.md, OpenCode
        # wants agent/<name>.md.
        payload = io.BytesIO()
        n = 0
        with tarfile.open(fileobj=payload, mode="w:gz") as tar:
            for src_name, dest, flatten in (
                ("skills", _SKILL_SUBDIR, False),
                ("scripts", _SCRIPT_SUBDIR, False),
                ("plugin", _PLUGIN_SUBDIR, False),
                ("sub_agents", _AGENT_SUBDIR, True),
            ):
                src = ws / src_name
                if not src.is_dir():
                    continue
                if flatten:
                    for p in sorted(src.glob("*/AGENT.md")):
                        tar.add(p, arcname=f"{dest}/{p.parent.name}.md")
                        n += 1
                else:
                    for p in sorted(src.rglob("*")):
                        if p.is_file():
                            tar.add(p, arcname=f"{dest}/{p.relative_to(src)}")
                            n += 1
        if n == 0:
            return

        blob = base64.b64encode(payload.getvalue()).decode()
        home_fix = 'export HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"; '
        dirs = " ".join(f'"$HOME/{d}"' for d in
                        (_SKILL_SUBDIR, _AGENT_SUBDIR, _PLUGIN_SUBDIR, _SCRIPT_SUBDIR))
        cmd = (
            home_fix
            + f"mkdir -p {dirs} && "
            + f"echo {blob} | base64 -d | tar -xzf - -C \"$HOME\" && "
            + f'chmod -R a+rX "$HOME/{_CONFIG_DIR}" "$HOME/{_SCRIPT_SUBDIR}"'
        )
        await self.exec_as_agent(environment, command=cmd)
        log = getattr(self, "logger", None)
        if log is not None:
            log.info("materialized %d harness file(s) from %s", n, ws)
