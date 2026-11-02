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

"""Trial identity: was this rollout finished, and by this configuration?

Completion is recorded explicitly by the code that observed the run return; presence of output
is not enough. The marker also fingerprints the harness that produced the rollout, and optionally
the task inputs the rollout answered, so a rollout from a different configuration -- or one
answering a task that has since been corrected -- is never reused.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

MARK = ".adapt_complete"


VERSION = 1


def _ignored(p: Path) -> bool:
    """Byproducts that are not part of the harness and must not change its identity."""
    return "__pycache__" in p.parts or p.suffix == ".pyc" or p.name == ".DS_Store"


def fingerprint(harness_dir: Path | None) -> str:
    """A stable digest of the harness tree that produced a rollout; `None` means a base
    rollout on the inherited harness and digests to the shared value "base".
    """
    if harness_dir is None:
        return "base"
    root = Path(harness_dir)
    if not root.is_dir():
        return "absent"
    h = hashlib.sha1()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name == MARK or _ignored(p):
            continue
        h.update(str(p.relative_to(root)).encode())
        h.update(b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()[:16]


def inputs_fingerprint(task_prompt: str, files_dir: Path | None) -> str:
    """A stable digest of what the agent was asked and what it was given.

    `task_prompt` is the model-visible task text (adapters already strip the answer key and the
    grading criteria from it, so those never enter the digest); `files_dir` is the source
    material from `BenchmarkAdapter.task_files`, walked in sorted order and hashed by relative
    path plus bytes. An adapter that exposes neither still digests to a stable value, so callers
    need no special case.
    """
    h = hashlib.sha1()
    h.update(b"prompt\0")
    h.update(str(task_prompt).encode())
    h.update(b"\0")
    root = Path(files_dir) if files_dir is not None else None
    if root is not None and root.is_dir():
        for p in sorted(root.rglob("*")):
            if not p.is_file() or _ignored(p):
                continue
            h.update(str(p.relative_to(root)).encode())
            h.update(b"\0")
            try:
                h.update(p.read_bytes())
            except OSError:
                h.update(b"<unreadable>")
            h.update(b"\0")
    return h.hexdigest()[:16]


def mark_complete(
    where: Path,
    *,
    run_id: str,
    harness_dir: Path | None = None,
    inputs: str | None = None,
    extra: dict | None = None,
) -> Path:
    """Record that `run_id` finished. Call only after the runner returned normally.

    `inputs` is `inputs_fingerprint` for the task this rollout answered, or `None` when the
    adapter does not expose task inputs; it is omitted from the marker rather than written as
    null so markers keep their previous shape for such adapters.
    """
    d = Path(where)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": VERSION,
        "run_id": run_id,
        "at": time.time(),
        "harness": fingerprint(harness_dir),
        **(extra or {}),
    }
    if inputs is not None:
        payload["inputs"] = inputs
    tmp = d / f"{MARK}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    final = d / MARK
    os.replace(tmp, final)
    return final


def read_mark(where: Path) -> dict | None:
    try:
        return json.loads((Path(where) / MARK).read_text())
    except Exception:
        return None


def is_complete(
    where: Path,
    *,
    harness_dir: Path | None = None,
    match_harness: bool = True,
    inputs: str | None = None,
) -> bool:
    """Whether the artifacts at `where` may be reused: a current marker naming the same
    harness and the same task inputs, unless either comparison is off.

    `inputs=None` keeps the previous behaviour of not comparing task inputs, and a marker
    written before input identity existed (no `inputs` key) stays reusable: rewriting identity
    for every in-flight run is a bigger decision than this check is worth.
    """
    m = read_mark(where)
    if not m or m.get("version") != VERSION:
        return False
    if inputs is not None and not _inputs_match(m, inputs):
        return False
    if not match_harness:
        return True
    return m.get("harness") == fingerprint(harness_dir)


def _inputs_match(m: dict, inputs: str | None) -> bool:
    """Legacy markers carry no input identity and are not invalidated by its arrival."""
    recorded = m.get("inputs")
    return recorded is None or inputs is None or recorded == inputs


def why_not(where: Path, *, harness_dir: Path | None = None, inputs: str | None = None) -> str:
    """A sentence for a log line explaining why `where` is or is not reusable."""
    m = read_mark(where)
    if not m:
        return f"no {MARK} -- never finished, or finished before completion was recorded"
    if m.get("version") != VERSION:
        return f"marker version {m.get('version')} != {VERSION}"
    if not _inputs_match(m, inputs):
        return (
            f"task inputs {m.get('inputs')} != {inputs} -- the task prompt or its source "
            f"files changed since this rollout finished"
        )
    want, got = fingerprint(harness_dir), m.get("harness")
    if want != got:
        return f"harness fingerprint {got} != {want} -- produced by a different configuration"
    return "reusable"
