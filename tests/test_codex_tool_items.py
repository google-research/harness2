# SPDX-License-Identifier: Apache-2.0

"""Replay Codex 0.144.6 protocol fixtures through the LAB trajectory consumer.

These are schema-derived protocol examples, not captures from a model-backed run.
See fixtures/codex_tool_items.md for the upstream producer and schema provenance.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.lab import LabAdapter
from harness2.substrate.codex import CodexSubstrate

FIXTURE = Path(__file__).parent / "fixtures" / "codex_tool_items.jsonl"


class CodexToolItemsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stream = self.root / "codex_session.jsonl"
        shutil.copyfile(FIXTURE, self.stream)
        self.adapter = LabAdapter(substrate="cx")

    def test_lab_trajectory_keeps_tool_actions_results_and_failures(self):
        rendered = self.adapter.trajectory(self.root, obs_trunc=50_000)
        for expected in (
            "[ACTION]",
            "search service.web_run",
            "OpenAI Codex CLI documentation",
            "search result",
            "permission denied",
            "answer.txt",
            "appendix.md",
            "missing.txt",
            "spawn_agent",
            "Review the totals",
            "Totals verified",
            "web_search",
            "https://example.org/report",
            "find_in_page",
            "Revenue",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, rendered)
        self.assertIn("[OBSERVATION ERROR] permission denied", rendered)
        self.assertIn("[OBSERVATION ERROR] failed", rendered)
        # Started/completed events share one item id; only the terminal state is shown.
        self.assertEqual(rendered.count("search service.web_run"), 1)
        self.assertNotIn("in_progress", rendered)

    def test_existing_shell_and_assistant_control(self):
        rendered = self.adapter.trajectory(self.root, obs_trunc=50_000)
        self.assertIn("[ACTION] shell: python scripts/check.py", rendered)
        self.assertIn("[OBSERVATION] checks passed", rendered)
        self.assertIn("[ASSISTANT] Deliverables are ready.", rendered)

    def test_tool_arguments_and_results_respect_existing_caps(self):
        item = {
            "id": "long-tool",
            "type": "mcp_tool_call",
            "server": "local",
            "tool": "read",
            "arguments": {"path": "a" * 100},
            "result": {"content": [{"type": "text", "text": "b" * 100}]},
            "error": None,
            "status": "completed",
        }
        self.stream.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
        rendered = CodexSubstrate().read_trajectory(
            self.stream, obs_trunc=48, args_trunc=32, max_chars=10_000
        )
        self.assertIn("[ACTION]", rendered)
        self.assertIn("[OBSERVATION]", rendered)
        self.assertNotIn("a" * 40, rendered)
        self.assertNotIn("b" * 60, rendered)
        self.assertIn("render truncation", rendered)
        capped = CodexSubstrate().read_trajectory(
            self.stream, obs_trunc=48, args_trunc=32, max_chars=20
        )
        self.assertEqual(capped[:20], rendered[:20])
        self.assertIn("render truncation", capped)
