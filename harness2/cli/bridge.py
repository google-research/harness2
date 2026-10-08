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

"""Serve a Responses-API endpoint on Vertex AI for the Codex executor."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
from pathlib import Path

from harness2 import config

LITELLM_CONFIG = """model_list:
  - model_name: {model}
    litellm_params:
      model: vertex_ai/{model}
      vertex_project: os.environ/GOOGLE_CLOUD_PROJECT
      vertex_location: os.environ/GOOGLE_CLOUD_LOCATION
litellm_settings:
  drop_params: true
general_settings:
  master_key: os.environ/HARNESS2_BRIDGE_KEY
"""


def _free_port(host: str) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _endpoint(path: Path, host: str, port: int) -> dict:
    """Reuse the saved port and key, so the Codex config written by setup stays valid."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    endpoint = {
        "port": port or saved.get("port") or _free_port(host),
        "key": saved.get("key") or secrets.token_urlsafe(24),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(endpoint) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return endpoint


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default=config.SOLVER_MODEL_CX, help="Vertex model ID to serve")
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface to bind; use 0.0.0.0 so WorkBuddy containers can reach it",
    )
    p.add_argument("--port", type=int, default=0, help="default: the saved port, else a free one")
    args = p.parse_args(argv)

    litellm = Path(sys.executable).with_name("litellm")
    if not litellm.is_file():
        p.exit(1, "LiteLLM is not installed; run: uv pip install -e '.[render,bridge]'\n")
    if not config.VERTEX_PROJECT:
        p.exit(1, "Set GOOGLE_CLOUD_PROJECT in .env\n")

    root = config.DEPENDENCIES / "codex"
    endpoint = _endpoint(root / "bridge.json", args.host, args.port)
    port = endpoint["port"]
    litellm_config = root / "bridge.yaml"
    litellm_config.write_text(LITELLM_CONFIG.format(model=args.model), encoding="utf-8")

    settings = {
        "HARNESS2_CODEX_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "HARNESS2_CODEX_API_KEY": endpoint["key"],
        "HARNESS2_CODEX_MODEL": args.model,
    }
    if args.host != "127.0.0.1":
        settings["HARNESS2_CODEX_CONTAINER_URL"] = f"http://host.docker.internal:{port}/v1"
    print("Add these to .env, then run harness2-setup <bench> --substrate cx:")
    print("\n".join(f"{name}={value}" for name, value in settings.items()), flush=True)

    env = {
        **os.environ,
        "HARNESS2_BRIDGE_KEY": endpoint["key"],
        "GOOGLE_CLOUD_PROJECT": config.VERTEX_PROJECT,
        "GOOGLE_CLOUD_LOCATION": config.VERTEX_REGION,
    }
    command = [str(litellm), "--config", str(litellm_config), "--host", args.host]
    os.execve(str(litellm), [*command, "--port", str(port)], env)


if __name__ == "__main__":
    raise SystemExit(main())
