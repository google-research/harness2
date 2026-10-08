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

"""Measurement of the run that selection chose, after it chose it.

Nothing in the method imports this module. A judge failure is None, never a zero, and a None is
excluded from the denominator.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .spec import BenchmarkAdapter


@dataclass
class Scored:
    task_id: str
    mode: str
    k: int
    label: str
    run_id: str
    chosen_is_base: bool
    n_passed: int | None = None
    n_criteria: int | None = None
    all_pass: bool = False
    raw: dict = field(default_factory=dict)
    error: str = ""

    @property
    def graded(self) -> bool:
        """Whether the judge succeeded, not whether the run did well."""
        return self.n_criteria is not None and self.n_criteria > 0

    @property
    def frac(self) -> float | None:
        return (self.n_passed / self.n_criteria) if self.graded else None


def _mode_of(sel: dict) -> str:
    """The mode token of a selection record, reading the legacy key too."""
    return str(sel.get("mode") if "mode" in sel else sel.get("arm") or "")


def judge_one(
    adapter: BenchmarkAdapter,
    task_id: str,
    mode: str,
    k: int,
    label: str,
    run_id: str,
    run_dir: Path,
    *,
    chosen_is_base: bool = False,
) -> Scored:
    s = Scored(
        task_id=task_id, mode=mode, k=k, label=label, run_id=run_id, chosen_is_base=chosen_is_base
    )
    try:
        got = adapter.score(run_dir, task_id)
    except Exception as exc:
        s.error = f"{type(exc).__name__}: {exc}"
        return s
    if not got:
        s.error = "judge returned nothing"
        return s
    s.raw = dict(got)
    s.n_passed = got.get("n_passed")
    s.n_criteria = got.get("n_criteria")
    s.all_pass = bool(got.get("all_pass", s.graded and s.n_passed == s.n_criteria))
    return s


def judge_selections(
    adapter: BenchmarkAdapter, selections: Sequence[dict], *, parallel: int = 8
) -> list[Scored]:
    """Judge each selection's chosen run, given Selection.to_dict() records."""
    out: list[Scored] = []
    if not selections:
        return out
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = {}
        for sel in selections:
            rd = sel.get("chosen_run_dir")
            if not sel.get("ok") or not sel.get("chosen_label") or not rd:
                out.append(
                    Scored(
                        task_id=sel.get("task_id", ""),
                        mode=_mode_of(sel),
                        k=sel.get("k", 0),
                        label=sel.get("chosen_label", ""),
                        run_id=sel.get("chosen_run_id", ""),
                        chosen_is_base=bool(sel.get("chosen_is_base")),
                        error="selection did not complete successfully",
                    )
                )
                continue
            futs[
                pool.submit(
                    judge_one,
                    adapter,
                    sel["task_id"],
                    _mode_of(sel),
                    sel["k"],
                    sel["chosen_label"],
                    sel.get("chosen_run_id", ""),
                    Path(rd),
                    chosen_is_base=bool(sel.get("chosen_is_base")),
                )
            ] = sel
        for f in as_completed(futs):
            try:
                out.append(f.result())
            except Exception as exc:
                sel = futs[f]
                out.append(
                    Scored(
                        task_id=sel.get("task_id", ""),
                        mode=_mode_of(sel),
                        k=sel.get("k", 0),
                        label=sel.get("chosen_label", ""),
                        run_id=sel.get("chosen_run_id", ""),
                        chosen_is_base=bool(sel.get("chosen_is_base")),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
    return out


def write(rows: Sequence[Scored], path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [r.__dict__ | {"frac": r.frac, "graded": r.graded} for r in rows], indent=2, default=str
        ),
        encoding="utf-8",
    )
    return path
