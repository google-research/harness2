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

"""LAB openai-compatible judge escape hatch (issue #95)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from harness2 import config

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "third_party" / "patches" / "harvey-labs-judge-vertex-anthropic.diff"
PIN = config.DEPENDENCY_PINS["harvey-labs"]
UPSTREAM_URL = (
    f"https://raw.githubusercontent.com/harveyai/harvey-labs/{PIN}/evaluation/judge.py"
)


def _load_patched_judge(module_dir: Path):
    """Import evaluation.judge from a patched checkout with stubbed SDKs."""
    for name in ("anthropic", "openai", "google", "google.genai", "mistralai", "mistralai.client"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules["anthropic"].Anthropic = object
    sys.modules["anthropic"].AnthropicVertex = object
    sys.modules["anthropic"].InternalServerError = type("InternalServerError", (Exception,), {})
    sys.modules["openai"].OpenAI = MagicMock(name="OpenAI")
    google = sys.modules["google"]
    google.genai = sys.modules["google.genai"]
    google.genai.Client = object
    google.genai.types = types.SimpleNamespace(GenerateContentConfig=object)
    sys.modules["mistralai.client"].Mistral = object

    # evaluation package for relative-less import
    package = types.ModuleType("evaluation")
    package.__path__ = [str(module_dir)]
    sys.modules["evaluation"] = package
    path = module_dir / "judge.py"
    spec = importlib.util.spec_from_file_location("evaluation.judge", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["evaluation.judge"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(PATCH.is_file(), "release patch assets not present")
class LabOpenAICompatibleJudgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            req = urllib.request.Request(UPSTREAM_URL, headers={"User-Agent": "harness2-tests"})
            upstream = urllib.request.urlopen(req, timeout=30).read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise unittest.SkipTest(f"could not fetch pinned LAB judge.py: {exc}") from exc

        cls._tmpdir = tempfile.TemporaryDirectory()
        repo = Path(cls._tmpdir.name)
        evaluation = repo / "evaluation"
        evaluation.mkdir()
        (evaluation / "judge.py").write_bytes(upstream)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "evaluation/judge.py"], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=dev@example.com", "-c", "user.name=dev", "commit", "-q", "-m", "init"],
            cwd=repo,
            check=True,
        )
        applied = subprocess.run(
            ["git", "-C", str(repo), "apply", str(PATCH)],
            capture_output=True,
            text=True,
        )
        if applied.returncode != 0:
            raise unittest.SkipTest(f"patch did not apply: {applied.stderr}")
        cls.judge_mod = _load_patched_judge(evaluation)

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()

    def test_unknown_model_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown judge provider"):
            self.judge_mod.Judge(model="deepseek-flash")

    def test_openai_compatible_prefix_builds_chat_client(self):
        OpenAI = self.judge_mod.openai.OpenAI
        OpenAI.reset_mock()
        with patch.dict(
            os.environ,
            {
                "HARNESS2_JUDGE_BASE_URL": "https://api.example.com/v1",
                "HARNESS2_JUDGE_API_KEY": "secret",
            },
            clear=False,
        ):
            # Clear SDK defaults that would otherwise win when set in the host env.
            os.environ.pop("OPENAI_BASE_URL", None)
            judge = self.judge_mod.Judge(model="openai-compatible/deepseek-flash")
        self.assertEqual(judge.provider, "openai-compatible")
        self.assertEqual(judge.model, "deepseek-flash")
        OpenAI.assert_called_once_with(
            base_url="https://api.example.com/v1",
            api_key="secret",
        )

    def test_openai_compatible_uses_chat_completions_not_responses(self):
        client = MagicMock()
        client.chat.completions.create.return_value = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content=json.dumps({"verdict": "pass", "reasoning": "ok"})
                    )
                )
            ]
        )
        with patch.dict(
            os.environ,
            {"HARNESS2_JUDGE_BASE_URL": "https://api.example.com/v1", "HARNESS2_JUDGE_API_KEY": "k"},
            clear=False,
        ):
            judge = self.judge_mod.Judge(model="openai-compatible:deepseek-chat")
        judge.client = client
        got = judge.evaluate("Return {value}", {"value": "json"}, _retries=2)
        self.assertEqual(got["verdict"], "pass")
        client.chat.completions.create.assert_called()
        client.responses.create.assert_not_called()
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "deepseek-chat")
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})

    def test_native_openai_path_still_uses_responses(self):
        client = MagicMock()
        client.responses.create.return_value = types.SimpleNamespace(
            output_text=json.dumps({"verdict": "fail", "reasoning": "no"})
        )
        judge = self.judge_mod.Judge(model="gpt-4.1-mini")
        judge.client = client
        got = judge.evaluate("x {v}", {"v": "1"}, _retries=2)
        self.assertEqual(got["verdict"], "fail")
        client.responses.create.assert_called()
        client.chat.completions.create.assert_not_called()

    def test_lab_judge_model_passes_openai_compatible_through(self):
        # config.LAB_JUDGE_MODEL only prefixes bare claude/gemini ids with vertex/.
        source = (ROOT / "harness2" / "config.py").read_text(encoding="utf-8")
        self.assertIn('if JUDGE_MODEL.startswith(("claude", "gemini"))', source)
        self.assertNotIn("openai-compatible", source.split("LAB_JUDGE_MODEL", 1)[1].split("\n)", 1)[0])

    def test_preflight_requires_base_url_for_openai_compatible_lab_judge(self):
        source = (ROOT / "harness2" / "preflight.py").read_text(encoding="utf-8")
        self.assertIn('startswith("openai-compatible")', source)
        self.assertIn("HARNESS2_JUDGE_BASE_URL", source)
        self.assertIn("OPENAI_BASE_URL", source)


if __name__ == "__main__":
    unittest.main()
