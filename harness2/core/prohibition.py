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

"""Prohibition replay: check newly added 'never/do not' harness rules against existing rollouts.

Addresses E.6 premise propagation (#199): when a candidate harness adds a prohibition rule,
replay its concrete target terms against already-executed reference and pool trajectories so
Compose receives factual match counts instead of inheriting an unverified prohibition blindly.
This module is strictly advisory and answer-free (it reads only trajectories and diffs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .spec import RunView

_PROHIBITION_RE = re.compile(
    r"\b(?:never|do\s+not|don't|must\s+not|mustn't|forbid(?:den)?|prohibit(?:ed)?|"
    r"disallow(?:ed)?|ban(?:ned)?|avoid\s+using|stop\s+using)\b",
    re.IGNORECASE,
)
_BACKTICK_RE = re.compile(r"`([^`\n]{2,80})`")
_QUOTED_RE = re.compile(r"""["']([^"'\n]{3,60})["']""")
_AFTER_VERB_RE = re.compile(
    r"\b(?:never|do\s+not|don't|must\s+not|forbid|prohibit|avoid)\s+"
    r"(?:use|call|run|invoke|inject|pass|add|write|include|enable)?\s*"
    r"([A-Za-z_][A-Za-z0-9_.\-/]{2,40})",
    re.IGNORECASE,
)

_STOP_TARGETS = frozenset(
    {
        "the",
        "this",
        "that",
        "any",
        "all",
        "from",
        "with",
        "into",
        "only",
        "just",
        "also",
        "more",
        "less",
        "make",
        "keep",
        "leave",
        "assume",
        "guess",
        "skip",
        "ignore",
        "forget",
        "change",
        "modify",
        "delete",
        "remove",
        "edit",
        "file",
        "files",
        "text",
        "line",
        "lines",
        "rule",
        "rules",
        "value",
        "values",
        "output",
        "result",
        "results",
        "answer",
        "prompt",
        "task",
        "harness",
    }
)


@dataclass(frozen=True)
class ProhibitionRule:
    """One prohibition line newly added by a candidate relative to its baseline."""

    file_rel: str
    line: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class ProhibitionFinding:
    """An added prohibition whose target already appeared in delivered rollout trajectories."""

    candidate: str
    file_rel: str
    line: str
    matched_targets: tuple[str, ...]
    matched_rollouts: tuple[str, ...]
    status: str = "unverified_prohibition"


def _extract_targets(line: str) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        cleaned = tok.strip().strip(".,:;()[]{}")
        low = cleaned.lower()
        if len(cleaned) < 3 or low in _STOP_TARGETS or low in seen:
            return
        seen.add(low)
        found.append(cleaned)

    for m in _BACKTICK_RE.finditer(line):
        _add(m.group(1))
    for m in _QUOTED_RE.finditer(line):
        _add(m.group(1))
    if not found:
        for m in _AFTER_VERB_RE.finditer(line):
            _add(m.group(1))
    return tuple(found[:5])


def extract_added_prohibitions(
    base_tree: Mapping[str, str], candidate_tree: Mapping[str, str]
) -> list[ProhibitionRule]:
    """Return prohibition rules added in `candidate_tree` that were absent from `base_tree`."""
    rules: list[ProhibitionRule] = []
    for rel in sorted(candidate_tree):
        base_lines = {
            ln.strip() for ln in (base_tree.get(rel, "") or "").splitlines() if ln.strip()
        }
        for raw_ln in (candidate_tree.get(rel, "") or "").splitlines():
            ln = raw_ln.strip()
            if not ln or ln in base_lines:
                continue
            if not _PROHIBITION_RE.search(ln):
                continue
            targets = _extract_targets(ln)
            if targets:
                rules.append(ProhibitionRule(file_rel=rel, line=ln[:240], targets=targets))
    return rules


def replay_prohibitions(
    candidate_label: str,
    base_tree: Mapping[str, str],
    candidate_tree: Mapping[str, str],
    rollouts: Sequence[RunView],
    read_trajectory: Callable[[RunView], str],
) -> list[ProhibitionFinding]:
    """Check whether added prohibitions match behaviors in already-delivered rollouts."""
    added = extract_added_prohibitions(base_tree, candidate_tree)
    if not added:
        return []

    delivered_trajs: list[tuple[str, str]] = []
    for rv in rollouts:
        if not (rv.ok and rv.delivered):
            continue
        try:
            body = read_trajectory(rv) or ""
        except Exception:
            continue
        if body.strip():
            delivered_trajs.append((rv.label, body.lower()))

    if not delivered_trajs:
        return []

    findings: list[ProhibitionFinding] = []
    for rule in added:
        hit_rollouts: list[str] = []
        hit_targets: list[str] = []
        for lbl, traj_low in delivered_trajs:
            matched_here = [t for t in rule.targets if t.lower() in traj_low]
            if matched_here:
                hit_rollouts.append(lbl)
                for t in matched_here:
                    if t not in hit_targets:
                        hit_targets.append(t)
        if hit_rollouts:
            findings.append(
                ProhibitionFinding(
                    candidate=candidate_label,
                    file_rel=rule.file_rel,
                    line=rule.line,
                    matched_targets=tuple(hit_targets),
                    matched_rollouts=tuple(sorted(set(hit_rollouts))),
                )
            )
    return findings


def check_pool_prohibitions(
    base_tree: Mapping[str, str],
    candidates: Sequence[tuple[str, Mapping[str, str]]],
    rollouts: Sequence[RunView],
    read_trajectory: Callable[[RunView], str],
) -> list[ProhibitionFinding]:
    """Replay added prohibitions for all candidates in a compose pool."""
    out: list[ProhibitionFinding] = []
    for label, cand_tree in candidates:
        out.extend(replay_prohibitions(label, base_tree, cand_tree, rollouts, read_trajectory))
    return out


def format_replay_report(findings: Sequence[ProhibitionFinding]) -> str:
    """Format factual prohibition-replay warnings for Compose (`prohibition_replay.md`)."""
    if not findings:
        return ""
    lines = [
        "# PROHIBITION REPLAY (FACTUAL TRAJECTORY CHECK)",
        "",
        "The following newly added prohibition rules match behaviors that ALREADY occurred in "
        "delivered reference/pool rollouts. This is an advisory signal, not an automatic veto: "
        "cite concrete contrast evidence showing the prohibited behavior caused a defect before "
        "adopting the rule, or drop/narrow it to avoid premise propagation.",
        "",
    ]
    for f in findings:
        targets = ", ".join(f"`{t}`" for t in f.matched_targets)
        runs = ", ".join(f"`{r}`" for r in f.matched_rollouts)
        lines.append(
            f"- **[{f.status}]** `{f.candidate}` (`{f.file_rel}`): matched {targets} in "
            f"{len(f.matched_rollouts)} delivered rollout(s) ({runs}) — rule: \"{f.line}\""
        )
    return "\n".join(lines) + "\n"
