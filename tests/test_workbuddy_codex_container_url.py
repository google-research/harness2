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

"""WorkBuddy's Codex preflight refuses a bridge URL its containers cannot reach."""

import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks.workbuddy import WbAdapter
from harness2.substrate.codex import CodexSubstrate


class WorkBuddyCodexContainerUrlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repo = root / "dependencies" / "workbuddy-bench"
        entry = repo / f"configs/harnesses/codex-harness/versions/{config.WB_CX_HARNESS_VER}.yaml"
        for path in (repo / "scripts/run.sh", entry, root / "codex/dist/codex"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        bridge = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(bridge.close)
        self.base_url = f"http://127.0.0.1:{bridge.getsockname()[1]}/v1"
        for patcher in (
            patch.object(config, "DEPENDENCIES", root / "dependencies"),
            patch.object(config, "CODEX_DIST", str(root / "codex/dist")),
            patch.object(config, "CODEX_BASE_URL", self.base_url),
            patch.dict(
                os.environ,
                {"HARNESS2_CODEX_BASE_URL": self.base_url, "HARNESS2_CODEX_API_KEY": "k"},
            ),
            patch("harness2.benchmarks.workbuddy.shutil.which", return_value="/usr/bin/uv"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def preflight(self, container_url):
        with patch.object(config, "CODEX_CONTAINER_URL", container_url):
            WbAdapter("office", substrate="cx").preflight()

    def test_loopback_container_url_is_refused(self):
        # Unset HARNESS2_CODEX_CONTAINER_URL defaults to the base URL: this loopback one.
        for url in (
            self.base_url,
            "http://localhost:4000/v1",
            "http://api.localhost:4000/v1",
            "http://127.0.1.1:4000/v1",
            "http://[::1]:4000/v1",
            "http://0.0.0.0:4000/v1",
        ):
            with self.subTest(url=url):
                with self.assertRaises(RuntimeError) as raised:
                    self.preflight(url)
                self.assertIn("HARNESS2_CODEX_CONTAINER_URL", str(raised.exception))

    def test_container_reachable_url_passes(self):
        for url in ("http://host.docker.internal:4000/v1", "https://responses.example.com/v1"):
            with self.subTest(url=url):
                self.preflight(url)

    def test_host_side_codex_keeps_the_loopback_bridge(self):
        # LAB and JobBench run Codex on the host, where the default loopback URL is right.
        with patch.object(config, "CODEX_CONTAINER_URL", self.base_url):
            CodexSubstrate().preflight()


if __name__ == "__main__":
    unittest.main()
