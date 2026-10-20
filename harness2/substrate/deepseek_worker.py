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

"""One owned SDK process. Credentials arrive through the environment, never JSON or argv."""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

from .deepseek import EVENTS, DeepSeekSettings, redact, write_json


def run(request: dict) -> int:
    import psutil
    from deepseek_harness import DeepSeekHarness

    settings = DeepSeekSettings(**request["settings"])
    run_dir = Path(request["run_dir"])
    secret = os.environ.get("DEEPSEEK_API_KEY", "")
    usage: dict[str, int | float] = {}
    seen = set()
    result = None
    error = ""
    harness = DeepSeekHarness(
        provider=settings.provider,
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        max_tokens=settings.max_tokens,
        cwd=request["workspace"],
        runtime_cwd=request["workspace"],
        dsh_home=request["home"],
        profile=settings.profile,
        patches=(request["patch"],),
        dsh_bin=settings.dsh_bin,
        initialize_timeout_seconds=settings.initialize_timeout,
        request_timeout_seconds=settings.initialize_timeout,
        shutdown_timeout_seconds=settings.shutdown_timeout,
        base_url=settings.base_url,
    )

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"DeepSeek worker interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, interrupted)

    with (run_dir / EVENTS).open("a", encoding="utf-8", buffering=1) as events:

        def capture(notification):
            record = {"method": notification.method, "params": notification.payload}
            events.write(redact(json.dumps(record, ensure_ascii=True), secret) + "\n")
            if notification.method != "session.event":
                return
            event = notification.payload.get("event")
            if not isinstance(event, dict):
                return
            key = (notification.payload.get("sessionId"), event.get("seq"))
            if key in seen:
                return
            seen.add(key)
            data = event.get("data")
            values = data.get("usage", event.get("usage")) if isinstance(data, dict) else None
            if isinstance(values, dict):
                for name, value in values.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        usage[name] = usage.get(name, 0) + value

        try:
            session = (
                harness.resume_session(request["session_id"])
                if request["resume"]
                else harness.start_session(request["session_id"])
            )
            result = session.run(request["prompt"], on_notification=capture)
            if result.finish_reason != "completed":
                raise RuntimeError(f"SDK turn did not complete: {result.finish_reason!r}")
            (run_dir / "final_response.txt").write_text(
                redact(result.final_response, secret), encoding="utf-8"
            )
        except (Exception, KeyboardInterrupt) as exc:
            error = redact(f"{type(exc).__name__}: {exc}", secret)
            events.write(
                json.dumps({"method": "harness2.error", "params": {"message": error}}) + "\n"
            )
            print(error, file=sys.stderr, flush=True)
        finally:
            children = psutil.Process().children(recursive=True)
            try:
                harness.close()
            finally:
                _, alive = psutil.wait_procs(children, timeout=settings.shutdown_timeout)
                for child in alive:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(alive, timeout=settings.shutdown_timeout)
                write_json(
                    run_dir / "metrics.json",
                    {
                        "session_id": request["session_id"],
                        "finish_reason": result.finish_reason if result else None,
                        "usage": usage,
                        "usage_scope": "invocation",
                        "cost_usd": None,
                        "error": error,
                    },
                )
    return int(bool(error))


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit("usage: python -m harness2.substrate.deepseek_worker REQUEST.json")
    request = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    return run(request)


if __name__ == "__main__":
    raise SystemExit(main())
