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

"""Judged selections -> the figure each benchmark reports, plus pick provenance and warnings.

Ungraded rows and tasks that never completed are excluded from the denominator rather than
counted as failures.
"""

from __future__ import annotations

import statistics as st
from dataclasses import dataclass
from typing import Sequence

from .judge import Scored
from .schedule import MODE_NAMES


@dataclass
class Summary:
    mode: str
    k: int
    n: int = 0
    n_ungraded: int = 0
    score: float | None = None
    all_pass: float | None = None
    kept_base: int = 0
    chose_final: int = 0
    chose_candidate: int = 0
    fell_back: int = 0
    usd: float = 0.0
    metrics: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return f"{MODE_NAMES.get(self.mode, self.mode)} k={self.k}"

    def line(self, bench: str = "") -> str:
        """LAB reports partial credit and all-criteria pass; the others report one score."""
        if not self.n:
            return f"  {self.name}: no graded task"
        figures = (
            f"partial {self.score:6.2f}% | pass {self.all_pass:5.1f}%"
            if bench == "lab"
            else f"score {self.score:6.2f}%"
        )
        pick = (
            f" | picked output: final {self.chose_final} / candidate {self.chose_candidate} / "
            f"base {self.kept_base}"
        )
        fb = f" | fell back {self.fell_back}" if self.fell_back else ""
        ug = f" | ungraded {self.n_ungraded}" if self.n_ungraded else ""
        return f"  {self.name}: n={self.n:>3} | {figures}{pick}{fb}{ug}"


def _kind(r: Scored) -> str:
    """Classify the selected output; each sequential checkpoint is a composed final."""
    lbl = str(getattr(r, "label", "") or "")
    if lbl.startswith("base"):
        return "base"
    if r.mode == "agg":
        return "final" if lbl.startswith("agg") else "candidate"
    if r.mode == "seq":
        return "final" if lbl in {f"c{index}" for index in range(1, r.k + 1)} else "candidate"
    return "candidate"


def summarise(
    rows: Sequence[Scored],
    *,
    fell_back: dict[tuple[str, int], int] | None = None,
    usd: dict[tuple[str, int], float] | None = None,
) -> list[Summary]:
    """One Summary per (mode, k). Ungraded rows are counted, then excluded."""
    keys = sorted({(r.mode, r.k) for r in rows})
    out: list[Summary] = []
    for mode, k in keys:
        group = [r for r in rows if r.mode == mode and r.k == k]
        good = [r for r in group if r.graded]
        units = tuple(
            sorted({str(r.raw.get("metric") or "") for r in good if isinstance(r.raw, dict)} - {""})
        )
        s = Summary(
            mode=mode,
            k=k,
            n=len(good),
            n_ungraded=len(group) - len(good),
            kept_base=sum(1 for r in good if r.chosen_is_base),
            chose_final=sum(1 for r in good if _kind(r) == "final"),
            chose_candidate=sum(1 for r in good if _kind(r) == "candidate"),
            fell_back=(fell_back or {}).get((mode, k), 0),
            usd=round((usd or {}).get((mode, k), 0.0), 2),
            metrics=units,
        )
        if good:
            s.score = round(100 * st.mean(r.frac for r in good), 2)
            s.all_pass = round(100 * sum(1 for r in good if r.all_pass) / len(good), 2)
        out.append(s)
    return out


def render(summaries: Sequence[Summary], *, title: str = "", bench: str = "") -> str:
    """The printable summary block."""
    lines = []
    if title:
        lines += [title, "=" * max(40, len(title))]
    for s in sorted(summaries, key=lambda x: (x.k, x.mode)):
        lines.append(s.line(bench))

    at1 = {s.mode: s for s in summaries if s.k == 1 and s.score is not None}
    if len(at1) == 2:
        gap = abs(at1["agg"].score - at1["seq"].score)
        lines += [
            "",
            "  NOTE  at k=1 the parallel and sequential modes are the same procedure "
            "(one INVARIANT draw, one DOMAIN draw, one composition).",
            f"        The {gap:.2f} pt between them is therefore a NOISE FLOOR, not a "
            f"result -- an effect at k>=2 has to clear it.",
        ]

    for s in summaries:
        if s.n and s.chose_final <= 0.25 * s.n:
            lines += [
                "",
                f"  WARNING  {s.name}: the mode's own final was chosen on only "
                f"{s.chose_final}/{s.n} tasks. These records include outputs outside "
                f"the published pool of {s.k} composed finals.",
            ]
    warn = [s for s in summaries if s.n and s.kept_base >= 0.8 * s.n]
    for s in warn:
        lines += [
            "",
            f"  WARNING  {s.name}: selection chose a reference output on "
            f"{s.kept_base}/{s.n} tasks, outside the published pool of composed outputs.",
        ]
    fb = [s for s in summaries if s.fell_back]
    for s in fb:
        lines += [
            "",
            f"  WARNING  {s.name}: {s.fell_back} stage(s) fell back to the "
            f"inherited harness. Inspect the stage errors when interpreting these results.",
        ]
    for s in summaries:
        if len(s.metrics) > 1:
            lines += [
                "",
                f"  WARNING  {s.name}: graded rows carry MIXED scoring units "
                f"({', '.join(s.metrics)}). The score averages weighted points with "
                f"unweighted criteria counts (about 20 pt apart on JobBench); re-judge "
                f"the fallback rows before comparing this line with any published "
                f"number.",
            ]
    return "\n".join(lines)
