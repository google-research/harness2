# SPDX-License-Identifier: Apache-2.0
"""Execute the real install shell using harmless version-tool functions."""

import asyncio
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_agent(filename, Base):
    async def ensure_agent_user(*args):
        return None

    exports = {
        "harbor.agents.installed.base": {"BaseInstalledAgent": Base},
        "harbor.environments.base": {"BaseEnvironment": object},
        "harbor.models.agent.context": {"AgentContext": object},
        "harbor.models.trajectories.agent": {"Agent": object},
        "harbor.models.trajectories.final_metrics": {"FinalMetrics": object},
        "harbor.models.trajectories.metrics": {"Metrics": object},
        "harbor.models.trajectories.step": {"Step": object},
        "harbor.models.trajectories.tool_call": {"ToolCall": object},
        "harbor.models.trajectories.trajectory": {"Trajectory": object},
        "workbuddy_bench.agents._agent_user": {"ensure_agent_user": ensure_agent_user},
    }
    modules = {}
    for name, attrs in exports.items():
        parts = name.split(".")
        for end in range(1, len(parts) + 1):
            key = ".".join(parts[:end])
            modules.setdefault(key, types.ModuleType(key))
        modules[name].__dict__.update(attrs)
    with patch.dict(sys.modules, modules):
        source = runpy.run_path(str(ROOT / "third_party/wb_runner/agents" / filename))
    return source


@unittest.skipUnless(shutil.which("bash"), "The pinned Harbor executor uses shell pipelines")
class WorkbuddyOpencodeVersionTests(unittest.TestCase):
    def test_install_refuses_wrong_version_and_preserves_pinned_build(self):
        for actual, pin in (
            ("1.14.18", "1.14.18"),
            ("1.13.0", "1.14.18"),
            ("11.14.18", "1.14.18"),
            ("1.13.0", None),
        ):
            with self.subTest(actual=actual, pin=pin), tempfile.TemporaryDirectory() as td:

                class Base:
                    async def exec_as_root(self, environment, command, env=None):
                        if env is not None:
                            # Package-manager bootstrap is outside this offline probe.
                            return None
                        functions = (
                            "opencode() { printf %s "
                            + shlex.quote(actual)
                            + "; }; rg() { printf 'ripgrep fixture\\n'; }; "
                        )
                        self.result = subprocess.run(
                            ["bash", "-c", "set -o pipefail; " + functions + command],
                            capture_output=True,
                            text=True,
                        )
                        if self.result.returncode:
                            raise RuntimeError(self.result.stderr)
                        return self.result

                source = load_agent("oc_agent.py", Base)
                agent = source["OcAgent"].__new__(source["OcAgent"])
                agent._oc_version = pin
                agent._mount_path = str(Path(td) / "missing-mount")
                if pin is None or actual == agent._oc_version:
                    asyncio.run(agent.install(None))
                else:
                    with self.assertRaises(RuntimeError):
                        asyncio.run(agent.install(None))

    def test_codex_install_uses_exact_cli_version(self):
        for actual, pin in (
            ("codex-cli 0.144.6", "0.144.6"),
            ("codex-cli 0.144.60", "0.144.6"),
            ("codex-cli 10.144.6", "0.144.6"),
            ("codex-cli 0.144.60", None),
        ):
            with self.subTest(actual=actual, pin=pin), tempfile.TemporaryDirectory() as td:

                class Base:
                    async def exec_as_root(self, environment, command, env=None):
                        # Shell-local functions intercept linking and the absolute version
                        # command. Neither /usr/local nor a real CLI is accessed.
                        functions = (
                            "ln() { :; }; function /usr/local/bin/codex { printf %s "
                            + shlex.quote(actual)
                            + "; }; "
                        )
                        result = subprocess.run(
                            ["bash", "-c", "set -o pipefail; " + functions + command],
                            capture_output=True,
                            text=True,
                        )
                        if result.returncode:
                            raise RuntimeError(result.stderr)
                        return result

                source = load_agent("codex_agent.py", Base)
                agent = source["CodexAgent"].__new__(source["CodexAgent"])
                agent._codex_version = pin
                agent._bin_dir = td
                binary = Path(td) / "codex"
                binary.write_text("unused fixture")
                binary.chmod(0o700)
                if pin is None or actual == "codex-cli 0.144.6":
                    asyncio.run(agent.install(None))
                else:
                    with self.assertRaises(RuntimeError):
                        asyncio.run(agent.install(None))
