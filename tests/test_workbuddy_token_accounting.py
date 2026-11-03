# SPDX-License-Identifier: Apache-2.0
"""OpenCode splits cached prompt tokens; Harbor totals include them again."""

import json
import runpy
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class Record:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def to_json_dict(self):
        def plain(value):
            if isinstance(value, Record):
                return {key: plain(val) for key, val in vars(value).items()}
            if isinstance(value, list):
                return [plain(val) for val in value]
            return value

        return plain(self)


class WorkbuddyTokenAccountingTests(unittest.TestCase):
    def test_prompt_totals_restore_cache_read_and_write_before_reporting_context(self):
        for cached in (False, True):
            with self.subTest(cached=cached), tempfile.TemporaryDirectory() as td:
                exports = {
                    "harbor.agents.installed.base": {"BaseInstalledAgent": object},
                    "harbor.environments.base": {"BaseEnvironment": object},
                    "harbor.models.agent.context": {"AgentContext": Record},
                    "harbor.models.trajectories.agent": {"Agent": Record},
                    "harbor.models.trajectories.final_metrics": {"FinalMetrics": Record},
                    "harbor.models.trajectories.metrics": {"Metrics": Record},
                    "harbor.models.trajectories.step": {"Step": Record},
                    "harbor.models.trajectories.tool_call": {"ToolCall": Record},
                    "harbor.models.trajectories.trajectory": {"Trajectory": Record},
                    "workbuddy_bench.agents._agent_user": {"ensure_agent_user": None},
                }
                modules = {}
                for name, attrs in exports.items():
                    parts = name.split(".")
                    for end in range(1, len(parts) + 1):
                        key = ".".join(parts[:end])
                        modules.setdefault(key, types.ModuleType(key))
                    modules[name].__dict__.update(attrs)
                with patch.dict(sys.modules, modules):
                    source = runpy.run_path(str(ROOT / "third_party/wb_runner/agents/oc_agent.py"))
                cls = source["OcAgent"]
                agent = cls.__new__(cls)
                agent.logs_dir = Path(td)
                agent._version = "1.14.18"
                agent.model_name = "fixture"
                agent.logger = types.SimpleNamespace(
                    debug=lambda *args: None, warning=lambda *args: None
                )
                reads, writes = (100, 30) if cached else (0, 0)
                event = {
                    "type": "step_finish",
                    "sessionID": "fixture",
                    "part": {
                        "tokens": {
                            "input": 20,
                            "output": 17,
                            "reasoning": 5,
                            "cache": {"read": reads, "write": writes},
                        },
                        "cost": 0.01,
                    },
                }
                # Two real step boundaries ensure both step and run aggregates agree.
                (agent.logs_dir / "oc-output.txt").write_text((json.dumps(event) + "\n") * 2)
                context = Record()
                agent.populate_context_post_run(context)
                self.assertEqual(context.n_input_tokens, 2 * (20 + reads + writes))
                self.assertEqual(context.n_cache_tokens, 2 * reads)
                self.assertEqual(context.n_output_tokens, 44)
                trajectory = json.loads((agent.logs_dir / "trajectory.json").read_text())
                for step in trajectory["steps"]:
                    self.assertEqual(step["metrics"]["prompt_tokens"], 20 + reads + writes)
                    if cached:
                        self.assertEqual(step["metrics"]["cached_tokens"], reads)
