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

"""Literal deployer policy, verified at the final execution input.

This module uses only the standard library. Setup copies it beside isolated benchmark
runners so they enforce exactly the same policy without installing harness2 there.
The policy preserves prompt text; it does not enforce model or tool behavior.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

BEGIN = "PINNED POLICY (fixed by the deployer; do not override)"
END = "END PINNED POLICY"
OPENCODE_MAX_CHARS = 24_000
CODEX_MAX_CHARS = 8_000
CODEX_MAX_BYTES = 32_768
ENV = "HARNESS2_PINNED_POLICY"


def block(policy: str) -> str:
    body = policy.strip()
    return f"{BEGIN}\n{body}\n{END}\n\n" if body else ""


def digest(policy: str) -> str:
    b = block(policy)
    return hashlib.sha256(b.encode("utf-8")).hexdigest() if b else ""


def reserved_chars(policy: str) -> int:
    return len(block(policy))


def check_size(text: str, *, max_chars: int, max_bytes: int | None = None) -> None:
    if len(text) > max_chars:
        raise ValueError(f"always-on content is {len(text)} chars, over the {max_chars} cap")
    if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
        raise ValueError(f"always-on content exceeds the {max_bytes} UTF-8 byte cap")


def verify_prompt(text: str, policy: str, *, max_chars: int, max_bytes: int | None = None) -> None:
    """Check the actual launch input, including limits applied by the runtime."""
    b = block(policy)
    if not b:
        return
    if not text.startswith(b):
        raise ValueError("pinned policy missing or altered; refusing to run without it")
    check_size(text, max_chars=max_chars, max_bytes=max_bytes)


def prepend(text: str, policy: str, *, max_chars: int, max_bytes: int | None = None) -> str:
    if not block(policy):
        return text
    result = block(policy) + text
    verify_prompt(result, policy, max_chars=max_chars, max_bytes=max_bytes)
    return result


def inject_file(target: Path, policy: str, *, max_chars: int, max_bytes: int | None = None) -> None:
    """Inject only after rendering/installation; read back the bytes handed to the CLI."""
    if not block(policy):
        return
    existing = target.read_bytes().decode("utf-8") if target.is_file() else ""
    result = prepend(existing, policy, max_chars=max_chars, max_bytes=max_bytes)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(result.encode("utf-8"))
    verify_file(target, policy, max_chars=max_chars, max_bytes=max_bytes)


def verify_file(target: Path, policy: str, *, max_chars: int, max_bytes: int | None = None) -> None:
    if not block(policy):
        return
    text = target.read_bytes().decode("utf-8") if target.is_file() else ""
    verify_prompt(text, policy, max_chars=max_chars, max_bytes=max_bytes)


def validate_tree(surface, staged: Path, policy: str) -> None:
    """Reserve policy space even for base trees and direct materialisation."""
    if not block(policy):
        return
    text = "\n\n".join(
        (staged / rel).read_bytes().decode("utf-8") if (staged / rel).is_file() else ""
        for rel in surface.always_on
    )
    prepend(
        text,
        policy,
        max_chars=surface.always_on_max,
        max_bytes=CODEX_MAX_BYTES if surface.name == "codex" else None,
    )


if __name__ == "__main__":
    import os
    import sys

    # The JobBench shell runner calls this on its installed workspace, before codex.
    inject_file(
        Path(sys.argv[1]) / "AGENTS.md",
        os.environ.get(ENV, ""),
        max_chars=CODEX_MAX_CHARS,
        max_bytes=CODEX_MAX_BYTES,
    )
