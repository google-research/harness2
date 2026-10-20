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

"""Credential-free Messages fixture driving the REAL SDK, plugins, tools, and sessions.

This scripted provider is a contract test, not a simulation of model quality.
It asks the runtime to execute the candidate's Python helper and deliver its stdout.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import threading
import uuid
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .benchmarks import deepseek as DS
from .core import tree
from .demo import DemoAdapter
from .substrate.base import Install, RunRequest
from .substrate.deepseek import EVENTS, DeepSeekSettings, DeepSeekSubstrate

FIXTURE_KEY = "harness2-local-fixture-not-a-credential"


class MessagesFixture:
    """An explicit loopback-only, unbilled provider; never a production fallback."""

    def __init__(self, *, port: int = 0, respond=None):
        self.requests: list[dict] = []
        self.errors: list[str] = []
        self.respond = respond or self.ledger_response
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                if self.path != "/v1/messages":
                    self.send_error(404, "Expected the native Messages API at /v1/messages")
                    return
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(request)
                try:
                    block = owner.respond(request)
                except (ValueError, KeyError, RuntimeError) as exc:
                    owner.errors.append(str(exc))
                    self.send_error(400, str(exc))
                    return
                payload = owner.sse(request["model"], block).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @staticmethod
    def sse(model: str, block: dict) -> str:
        tool = block["type"] == "tool_use"
        start = {**block, "input": {}} if tool else {"type": "text", "text": ""}
        delta = (
            {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
            if tool
            else {"type": "text_delta", "text": block["text"]}
        )
        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_" + uuid.uuid4().hex,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 16, "output_tokens": 0},
                },
            },
            {"type": "content_block_start", "index": 0, "content_block": start},
            {"type": "content_block_delta", "index": 0, "delta": delta},
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "tool_use" if tool else "end_turn",
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": 8},
            },
            {"type": "message_stop"},
        ]
        return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)

    @staticmethod
    def ledger_response(request: dict) -> dict:
        system = request.get("system", "")
        if isinstance(system, list):
            system = "\n".join(block.get("text", "") for block in system)
        match = re.search(r"Harness directory: ([^\r\n]+)", system)
        if not match:
            raise ValueError("Fixture requires a materialized Harness2 candidate")
        bundle = Path(match.group(1))
        workspace = bundle.parent / "workspace"
        tools = {tool["name"]: tool for tool in request["tools"]}
        calls = [
            block
            for message in request.get("messages", [])
            for block in message.get("content", [])
            if block.get("type") == "tool_use"
        ]
        if not calls:
            shell = "pwsh" if os.name == "nt" else "bash"
            if shell not in tools:
                raise ValueError(f"Native shell {shell} absent; offered tools: {sorted(tools)}")
            source = next(
                (
                    workspace / name / "ledger.csv"
                    for name in ("source", "task_folder", "documents")
                    if (workspace / name / "ledger.csv").is_file()
                ),
                workspace / "source" / "ledger.csv",
            )
            argv = [sys.executable, str(bundle / "scripts" / "summarize.py"), str(source)]
            command = (
                "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
                if os.name == "nt"
                else shlex.join(argv)
            )
            return {
                "type": "tool_use",
                "id": "call_" + uuid.uuid4().hex,
                "name": shell,
                "input": {"command": command},
            }
        if len(calls) == 1:
            results = [
                block
                for message in request["messages"]
                for block in message.get("content", [])
                if block.get("type") == "tool_result"
            ]
            text = json.dumps(results)
            total = re.search(r"Total:\s*(-?\d+\.\d{2})", text)
            if total is None:
                raise ValueError(f"Candidate script did not return a total: {text[-2000:]}")
            if "write" not in tools:
                raise ValueError(f"write absent; offered tools: {sorted(tools)}")
            return {
                "type": "tool_use",
                "id": "call_" + uuid.uuid4().hex,
                "name": "write",
                "input": {
                    "file_path": str(workspace / "output" / "answer.txt"),
                    "content": f"Total: {total.group(1)}\n",
                },
            }
        return {
            "type": "text",
            "text": "The candidate helper ran; the resulting total was delivered.",
        }


class DeepSeekDemoAdapter(DemoAdapter):
    """The existing two-ledger example, routed through the production DeepSeek substrate."""

    def __init__(self, root: Path, *, settings: DeepSeekSettings, api_key: str | None = None):
        super().__init__(root)
        self.substrate = DeepSeekSubstrate(
            launcher=self._launch,
            settings=settings,
            api_key=api_key,
        )
        self.spec = replace(self.spec, name="deepseek-demo", install_mode="deepseek_profile")
        base = tree.read_tree(self.substrate.surface(), self.base_harness())
        base["systemprompt.md"] += "Write the computed total to output/answer.txt.\n"
        tree.write_tree(self.substrate.surface(), base, self.base_harness(), stubs=False)

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        run_dir = self.root / "executions" / run_id
        return DS.execute(
            self,
            task_id,
            harness_dir,
            run_id,
            timeout=timeout or 120,
            evidence_dir=run_dir,
            output_dir=run_dir,
        )

    def _launch(self, req: RunRequest, install: Install) -> list[Path]:
        self.execution_count += 1
        return DS.launch(
            self,
            req,
            install,
            evidence_dir=self.root / "executions" / req.run_ids[0],
            source_name="source",
        )

    def validate_tree(self, harness: tree.Tree) -> None:
        from .substrate.deepseek import validate_tree

        validate_tree(harness)
        super().validate_tree(harness)

    def delivered(self, run_dir: Path) -> bool:
        return (run_dir / "output" / "answer.txt").is_file()

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        return self.substrate.read_trajectory(
            run_dir / EVENTS,
            obs_trunc=obs_trunc,
            max_chars=8_000_000,
        )

    def deliverable_text(self, run_dir: Path) -> str:
        path = run_dir / "output" / "answer.txt"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def artifacts_intact(self, run_dir: Path) -> bool:
        return DS.artifacts_intact(self, evidence_dir=run_dir, output_dir=run_dir)

    def run_metrics(self, run_dir: Path) -> dict:
        return DS.run_metrics(run_dir)

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        # Invoked only by the separate evaluation command, never by refinement.
        if not self.substrate.artifacts_intact(run_dir):
            return None
        expected = {"ledger-1": "Total: 85.15\n", "ledger-2": "Total: 20.25\n"}[task_id]
        passed = self.deliverable_text(run_dir) == expected
        return {"n_passed": int(passed), "n_criteria": 1, "all_pass": passed}
