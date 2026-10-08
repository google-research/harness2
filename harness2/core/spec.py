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

"""The benchmark seam: everything a benchmark must supply, and nothing more.

Three axes stay separate -- method (recursion, prompts, selection, library), substrate (the
agent framework a solver rollout runs under) and benchmark (a `BenchSpec` value plus an
adapter). `ChannelSpec` describes the proposer/selector channel, which is independent of both.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .. import config
from .tree import SurfaceSpec

ModelId = str

CHANNELS = ("claude_cli", "opencode")


@dataclass(frozen=True)
class ChannelSpec:
    """How to reach one model, on one proposer/selector channel."""

    kind: str
    model: ModelId
    effort: str = "high"
    steps: int = 120
    timeout: int = 3600

    def __post_init__(self) -> None:
        if self.kind not in CHANNELS:
            raise ValueError(f"unknown channel {self.kind!r}; expected one of {CHANNELS}")


def resolve_channel_model(
    kind: str | None,
    model: str | None,
    *,
    effort: str = "high",
    steps: int = 120,
    timeout: int = 3600,
) -> ChannelSpec:
    """The one channel->model mapping; a lone override of either raises."""
    use_default = kind is None and model is None
    if (kind is None) != (model is None):
        raise ValueError(
            "channel and model must be overridden TOGETHER (a bare-id/provider-prefixed "
            f"mismatch fails silently downstream); got kind={kind!r}, model={model!r}"
        )
    if kind is None:
        kind = config.DEFAULT_PROPOSER_CHANNEL
    if kind not in CHANNELS:
        raise ValueError(f"unknown channel {kind!r}; expected one of {CHANNELS}")
    if model is None:
        model = config.PROPOSER_MODEL_BY_CHANNEL[kind]
    if not model.strip():
        if use_default:
            return ChannelSpec(kind, "", effort=effort, steps=steps, timeout=timeout)
        raise ValueError("model override must not be empty")
    if kind == "opencode" and "/" not in model:
        raise ValueError(
            f"opencode channel needs a provider-prefixed model id "
            f"in HARNESS2_IMPROVER_MODEL or HARNESS2_SOLVER_MODEL, got {model!r}"
        )
    if kind == "claude_cli" and "/" in model:
        raise ValueError(
            f"claude_cli channel takes a bare Anthropic id "
            f"(e.g. {config.PROPOSER_MODEL_BY_CHANNEL['claude_cli']!r}), got {model!r}"
        )
    return ChannelSpec(kind, model, effort=effort, steps=steps, timeout=timeout)


@dataclass(frozen=True)
class BenchPrompts:
    """The benchmark-shaped text the shared prompts interpolate."""

    work: str
    domain_word: str = "domain"
    inv_levers: str = ""
    dom_levers: str = ""
    yardstick: str = ""
    deliverable_word: str = "deliverable"
    domain_guidance: str = ""
    selection_checks: str = ""
    selection_lead: str = ""
    selection_conformance: str = "full"
    prompt_pack: str = "adapt"

    def __post_init__(self) -> None:
        if self.prompt_pack not in ("adapt", "tts"):
            raise ValueError(
                f"BenchPrompts.prompt_pack must be 'adapt' or 'tts', got {self.prompt_pack!r}"
            )

        visible = {
            "work": self.work,
            "inv_levers": self.inv_levers,
            "dom_levers": self.dom_levers,
            "yardstick": self.yardstick,
            "domain_guidance": self.domain_guidance,
            "selection_checks": self.selection_checks,
        }
        for field_name, value in visible.items():
            low = value.lower()
            for bad in ("benchmark", "evaluation", "grader", "rubric", "score"):
                if bad in low:
                    raise ValueError(
                        f"BenchPrompts.{field_name} must describe the WORK, not its "
                        f"measurement (found {bad!r}). Say what the practitioner needs."
                    )


INSTALL_MODES = ("workspace_agents_md", "home_dir_jinja", "workspace_config", "codex_root")


@dataclass(frozen=True)
class BenchSpec:
    """Static description of one (benchmark, substrate) cell. A value, not a code path."""

    name: str
    runs_root: Path
    install_mode: str
    prompts: BenchPrompts
    channels: dict[str, ChannelSpec]
    domain_word: str = "domain"
    leak_globs: tuple[str, ...] = ()
    allow_subagents: bool = False

    reserved_scripts: frozenset[str] = frozenset()
    proposer_deliverables: str = "contents"
    state_deliverable: bool = False
    obs_trunc: int = 50_000

    def __post_init__(self) -> None:
        if self.install_mode not in INSTALL_MODES:
            raise ValueError(
                f"unknown install_mode {self.install_mode!r}; expected one of {INSTALL_MODES}"
            )
        if self.proposer_deliverables not in ("contents", "names"):
            raise ValueError(
                f"BenchSpec.proposer_deliverables must be 'contents' or 'names', got "
                f"{self.proposer_deliverables!r}"
            )
        for role in ("proposer", "selector"):
            if role not in self.channels:
                raise ValueError(f"BenchSpec.channels must define {role!r}")
        if self.obs_trunc < 10_000:
            raise ValueError(
                f"obs_trunc={self.obs_trunc} is in the range that fabricates defects; "
                f"use >= 10_000 (default 50_000)"
            )

        bad = sorted(s for s in self.reserved_scripts if "/" in s or not s.endswith(".py"))
        if bad:
            raise ValueError(
                f"BenchSpec.reserved_scripts takes bare .py BASENAMES, not paths: {bad}"
            )


@dataclass
class RunView:
    """What the method knows about one finished rollout. Never carries a score."""

    label: str
    run_id: str
    run_dir: Path
    ok: bool
    delivered: bool
    cost_usd: float = 0.0
    wall_s: float = 0.0
    error: str = ""


@runtime_checkable
class SubstrateLike(Protocol):
    """What core needs of a substrate; adapters hand core an object satisfying it."""

    name: str

    def surface(self) -> SurfaceSpec:
        """The editable c1..c8 tree schema + validators for this substrate."""


@runtime_checkable
class BenchmarkAdapter(Protocol):
    """Implement these and the whole method works. Nothing here is method logic."""

    spec: BenchSpec
    substrate: SubstrateLike

    def tasks(self, split: str = "all") -> list[str]:
        """Task ids for a split. Stable order -- library state must be reproducible."""

    def domain_of(self, task_id: str) -> str:
        """The grouping unit: practice area / occupation / role / sector."""

    def task_prompt(self, task_id: str) -> str:
        """The task as the agent received it, with criteria and expected output stripped."""

    def task_files(self, task_id: str) -> Path | None:
        """Optional. The source material the work was done against, or None."""

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        """Run one rollout and return its run directory, idempotently by run_id.

        `harness_dir=None` means the unmodified base harness.
        """

    def delivered(self, run_dir: Path) -> bool:
        """Did this rollout produce the artifact the task asked for?"""

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        """Readable trajectory in the shared [ACTION]/[OBSERVATION]/[ERROR] grammar."""

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        """Optional. filename -> extracted readable text, one entry per artifact produced."""

    def deliverable_text(self, run_dir: Path) -> str:
        """The artifact as text, extracted from agent-authored bytes only."""

    def base_harness(self) -> Path:
        """A directory holding the unmodified c1..c8 tree this benchmark starts from."""

    def validate_tree(self, tree: dict[str, str]) -> None:
        """Benchmark-specific harness invariants. Raise ValueError listing all problems in
        actionable prose: the message is fed back verbatim as retry feedback.
        """

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        """Judge one run. `None` means the judge failed, which is not a zero and is never
        cached as one. Called only by `core.judge`, only after selection committed.
        """


def resolve_prompts(adapter: BenchmarkAdapter, task_id: str) -> BenchPrompts:
    """Return the task-specific prompt profile when the adapter supplies one."""
    fn = getattr(adapter, "prompts_for", None)
    prompts = fn(task_id) if callable(fn) else adapter.spec.prompts
    if not isinstance(prompts, BenchPrompts):
        raise TypeError(
            f"{type(adapter).__name__}.prompts_for({task_id!r}) returned "
            f"{type(prompts).__name__}, expected BenchPrompts"
        )
    return prompts


__all__ = [
    "BenchSpec",
    "BenchPrompts",
    "ChannelSpec",
    "BenchmarkAdapter",
    "SubstrateLike",
    "RunView",
    "ModelId",
    "resolve_prompts",
    "resolve_channel_model",
    "CHANNELS",
    "INSTALL_MODES",
]
