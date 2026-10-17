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

"""Optional human feedback on a task: one Markdown file per task in a feedback directory.

    <feedback-dir>/<task-slug>.md      free text from the person running the sweep

Off unless a directory is passed. The text is served to this task's selector and experience
session, never to its proposers; what those two sessions take from it reaches later tasks in
the domain through the library. It is a hint toward the answer, so benchmark runs leave it
off. Feedback containing a grader-output marker is refused.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from . import context
from .library import slug


def path_for(feedback_dir: Path | str, task_id: str) -> Path:
    return Path(feedback_dir) / f"{slug(task_id)}.md"


def _text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} is not UTF-8 text: {exc}") from None
    for marker in context.GRADER_VERDICT_MARKERS:
        if marker in text:
            raise ValueError(
                f"{path} contains grader-output marker {marker!r}; feedback is notes for the "
                f"selector and the reflector, not grader output"
            )
    return text


def read(feedback_dir: Path | str | None, task_id: str) -> str:
    """The task's feedback text, or "" when there is no directory, no file, or an empty one.

    The file name must match exactly, also on case-insensitive file systems.
    """
    if feedback_dir is None:
        return ""
    root = Path(feedback_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"feedback directory {root} does not exist")
    path = path_for(root, task_id)
    if path.name not in {p.name for p in root.iterdir() if p.is_file()}:
        return ""
    return _text(path)


def check_dir(feedback_dir: Path | str, task_ids: Iterable[str]) -> list[str]:
    """Read every file before the first rollout; return the names that match no task.

    Raises on a missing directory, a file that is not UTF-8, or a grader-output marker, so a
    bad note fails the run up front rather than one task partway through a stream.
    """
    root = Path(feedback_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"feedback directory {root} does not exist")
    expected = {f"{slug(t)}.md" for t in task_ids}
    unmatched = []
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        if p.name in expected:
            _text(p)
        else:
            unmatched.append(p.name)
    return unmatched


def fingerprint(text: str) -> str:
    """Recorded on the summary so a resumed task re-runs when its feedback changes."""
    return hashlib.sha1(text.encode()).hexdigest()[:16] if text else ""
