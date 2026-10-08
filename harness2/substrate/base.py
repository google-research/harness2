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

"""The Substrate interface: what an agent framework owns, benchmark-free."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol, runtime_checkable

from ..core.tree import SurfaceSpec


@dataclass(frozen=True)
class Install:
    """A materialised harness ready for a launcher; `fingerprint` digests the SOURCE tree."""

    staging_dir: Path
    fingerprint: str
    extras: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunRequest:
    """One execution request handed to a benchmark-supplied launcher body."""

    task_ref: str
    run_ids: tuple[str, ...]
    timeout: int


Launcher = Callable[[RunRequest, Install], list[Path]]


@runtime_checkable
class Substrate(Protocol):
    """The full substrate interface; `core.spec.SubstrateLike` is the subset core needs."""

    name: str

    def surface(self) -> SurfaceSpec:
        """The editable c1..c8 tree schema + validators for this substrate."""

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage the editable tree as substrate-native files, per `spec.install_mode`.

        `ctx` carries benchmark-owned values: {{token}} interpolation slots and `file:<rel>`
        entries for integration files that ride along with the staged tree.
        """

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        """Run rollouts via the benchmark-supplied launcher; one run_dir per run_id."""

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400
    ) -> str:
        """Render this substrate's capture format into the shared grammar.

        `obs_trunc` and `max_chars` are carried on the cell's BenchSpec, never module-global
        state. `args_trunc` is a per-substrate default (400 for opencode, 2000 for codex) that
        BenchSpec does not carry and no caller overrides.
        """

    def preflight(self) -> None:
        """Health checks before money is spent; raises RuntimeError with the remediation
        command and never starts any part of the model chain."""


__all__ = ["Install", "RunRequest", "Launcher", "Substrate"]
