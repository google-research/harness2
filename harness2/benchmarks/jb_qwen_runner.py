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

"""JobBench Qwen execution; owns the actual CLI process group and durable diagnostics."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from ..core import runmark as RM
from ..substrate.qwen import (
    PROFILE_VERSION,
    PROTOCOL_VERSION,
    QwenSubstrate,
    digest,
    parse_events,
    redact,
)
from .jb_opencode_runner import PROMPT


def file_digest(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_attempt(out: Path, trajectory: Path) -> None:
    if not out.exists() and not trajectory.exists():
        return
    root = trajectory.parent / "_failed_attempts" / trajectory.name
    attempt = root / str(time.time_ns())
    attempt.mkdir(parents=True)
    for source, name in ((out, "output"), (trajectory, "trajectory")):
        if source.exists():
            shutil.move(str(source), attempt / name)
    for old in sorted(root.iterdir())[:-3]:
        shutil.rmtree(old)


def capture_identity(trajectory: Path) -> dict:
    return {
        "qwen_capture": file_digest(trajectory / "qwen-output.jsonl"),
        "qwen_status": file_digest(trajectory / "qwen-status.json"),
    }


def reusable(trajectory: Path, identity: dict) -> bool:
    try:
        marker = RM.read_mark(trajectory) or {}
        status = json.loads((trajectory / "qwen-status.json").read_text())
        if not isinstance(marker, dict) or not isinstance(status, dict):
            return False
        return (
            marker.get("version") == RM.VERSION
            and status["ok"] is True
            and status["identity"] == digest(identity)
            and marker.get("qwen_identity") == digest(identity)
            and all(marker.get(k) == v for k, v in capture_identity(trajectory).items())
            and parse_events(trajectory / "qwen-output.jsonl").ok
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


class QwenRunner:
    def __init__(self, substrate: QwenSubstrate):
        self.substrate = substrate
        self._lock = threading.RLock()
        self._active: set[subprocess.Popen] = set()
        self._cancelled = threading.Event()

    @staticmethod
    def _signal_group(proc: subprocess.Popen, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            active = list(self._active)
        self._stop(active)

    def _stop(self, processes: list[subprocess.Popen]) -> None:
        for proc in processes:
            self._signal_group(proc, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            alive = []
            for proc in processes:
                proc.poll()
                try:
                    os.killpg(proc.pid, 0)
                    alive.append(proc)
                except ProcessLookupError:
                    pass
            if not alive:
                break
            time.sleep(0.05)
        for proc in processes:
            self._signal_group(proc, signal.SIGKILL)
            proc.wait()

    @contextlib.contextmanager
    def scope(self):
        """Install signal cleanup on the main thread before entering scheduler worker pools."""
        previous = {}

        def interrupted(signum, frame):
            self.cancel()
            raise KeyboardInterrupt

        if threading.current_thread() is threading.main_thread():
            previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            for sig in previous:
                signal.signal(sig, interrupted)
        try:
            yield
        finally:
            self.cancel()
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def run(
        self,
        *,
        task_folder: Path,
        install,
        out: Path,
        trajectory: Path,
        timeout: int,
        identity: dict,
    ) -> Path:
        if timeout <= 0:
            raise ValueError("Qwen rollout timeout must be positive")
        trajectory.mkdir(parents=True, exist_ok=True)
        stdout_path = trajectory / "qwen-output.jsonl"
        stderr_path = trajectory / "qwen-stderr.txt"
        status_path = trajectory / "qwen-status.json"
        status = {
            "ok": False,
            "identity": digest(identity),
            "profile": PROFILE_VERSION,
            "protocol": PROTOCOL_VERSION,
            "returncode": None,
            "error": "",
        }
        start = time.monotonic()
        proc = None
        failure = None
        with tempfile.TemporaryDirectory(prefix="harness2-qwen-run-") as temporary:
            root = Path(temporary).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            try:
                shutil.copytree(install.staging_dir, workspace, dirs_exist_ok=True)
                shutil.copytree(task_folder, workspace / "task_folder")
                (workspace / "output").mkdir()
                env = self.substrate.exec_env(qwen_home=root / "home", workspace=workspace)
                # Qwen stops ancestor context discovery at the nearest project root.
                subprocess.run(
                    [
                        "git",
                        "-c",
                        "core.hooksPath=/dev/null",
                        "init",
                        "--quiet",
                        "--template=",
                        str(workspace),
                    ],
                    env={key: value for key, value in env.items() if key != "OPENAI_API_KEY"},
                    capture_output=True,
                    check=True,
                    timeout=15,
                )
                argv = self.substrate.exec_argv(install=install, timeout=timeout)
                prompt = PROMPT.format(
                    task_folder=workspace / "task_folder",
                    output_dir=workspace / "output",
                    workspace=workspace,
                )
                with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                    with self._lock:
                        if self._cancelled.is_set():
                            raise RuntimeError("Qwen execution cancelled")
                        proc = subprocess.Popen(
                            argv,
                            cwd=workspace,
                            env=env,
                            stdin=subprocess.PIPE,
                            stdout=stdout,
                            stderr=stderr,
                            start_new_session=True,
                        )
                        self._active.add(proc)
                    try:
                        proc.communicate(input=prompt.encode(), timeout=timeout + 2)
                    except subprocess.TimeoutExpired:
                        status["error"] = "Qwen wall timeout"
                        self._stop([proc])
                    finally:
                        self._stop([proc])
                        with self._lock:
                            self._active.discard(proc)
                status["returncode"] = proc.returncode
                if self._cancelled.is_set():
                    status["error"] = "Qwen execution cancelled"
                transcript = parse_events(stdout_path)
                for path in (stdout_path, stderr_path):
                    path.write_text(redact(path.read_text(errors="replace")), encoding="utf-8")
                status["result"] = transcript.result
                status["protocol_errors"] = transcript.errors
                status["install"] = json.loads((workspace / ".harness2/install.json").read_text())
                files = list((workspace / "output").rglob("*"))
                if any(path.is_symlink() for path in files):
                    raise RuntimeError("Qwen deliverables contain a symbolic link")
                if any(path.is_file() for path in files):
                    shutil.copytree(workspace / "output", out, dirs_exist_ok=True)
                if status["error"] or proc.returncode != 0 or not transcript.ok:
                    raise RuntimeError(
                        status["error"]
                        or f"Qwen exit={proc.returncode}: " + "; ".join(transcript.errors)
                    )
                if not any(path.is_file() for path in files):
                    raise RuntimeError("Qwen produced no final deliverable")
                status["ok"] = True
            except BaseException as exc:
                failure = exc
                status["error"] = redact(str(exc)) or type(exc).__name__
            finally:
                if proc is not None:
                    with self._lock:
                        registered = proc in self._active
                    if registered:
                        self._stop([proc])
                        with self._lock:
                            self._active.discard(proc)
                    status["returncode"] = proc.returncode
                for path in (stdout_path, stderr_path):
                    if path.exists():
                        path.write_text(redact(path.read_text(errors="replace")), encoding="utf-8")
                status["wall_s"] = round(time.monotonic() - start, 3)
                tmp = status_path.with_suffix(".tmp")
                tmp.write_text(
                    redact(json.dumps(status, sort_keys=True, indent=2)), encoding="utf-8"
                )
                os.replace(tmp, status_path)
        if failure is not None:
            if isinstance(failure, (KeyboardInterrupt, SystemExit)):
                raise failure
            raise RuntimeError(
                f"Qwen rollout incomplete: {status['error']}; see {trajectory}"
            ) from None
        return out
