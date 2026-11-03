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

"""Budget accounting: R = 4k rollouts and P = 3k improvement stages.

Base executions count toward each mode. Retries are separate invocation counts;
selection and reflection are recorded outside this ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def expected_labels(mode: str, k: int) -> tuple[set[str], set[str]]:
    """(rollout labels, session labels) the plan for `mode` produces.

    The budget identity is these STAGES, not a pair of counts: R = 4k rollouts (k
    shared base + 2k dimension candidates + k finals) and P = 3k sessions (k INV +
    k DOM + k finals). The finals are `agg1..aggk` in the parallel mode and
    `c1..ck` in the sequential one.

    Stating the plan once is what lets the ledger say that a run produced the right
    number of stages under the wrong names -- a candidate the ledger counts that was
    never actually produced.
    """
    n = int(k)
    if mode == "agg":
        finals = {f"agg{i}" for i in range(1, n + 1)}
    elif mode == "seq":
        finals = {f"c{i}" for i in range(1, n + 1)}
    else:
        raise ValueError(f"unknown mode {mode!r}; expected 'agg' or 'seq'")
    candidates = {f"inv{i}" for i in range(1, n + 1)} | {f"dom{i}" for i in range(1, n + 1)}
    base = {f"base{i}" for i in range(1, n + 1)}
    return base | candidates | finals, candidates | finals


class BudgetError(AssertionError):
    """The ledger did not land on the budget identity."""


@dataclass
class Entry:
    kind: str
    label: str
    usd: float = 0.0
    cached: bool = False
    attempts: int = 1


@dataclass
class Ledger:
    """Everything one mode spent on one task, recorded as it happens."""

    mode: str
    task_id: str
    k: int
    entries: list[Entry] = field(default_factory=list)

    def rollout(self, label: str, usd: float = 0.0, *, cached: bool = False) -> None:
        self.entries.append(Entry("rollout", label, usd, cached))

    def session(
        self, label: str, usd: float = 0.0, *, cached: bool = False, attempts: int = 1
    ) -> None:
        self.entries.append(Entry("session", label, usd, cached, attempts=attempts))

    @property
    def R(self) -> int:
        return sum(1 for e in self.entries if e.kind == "rollout")

    @property
    def P(self) -> int:
        return sum(1 for e in self.entries if e.kind == "session")

    @property
    def usd(self) -> float:
        return round(sum(e.usd for e in self.entries), 6)

    @property
    def rollout_usd(self) -> float:
        return round(sum(e.usd for e in self.entries if e.kind == "rollout"), 6)

    @property
    def session_usd(self) -> float:
        return round(sum(e.usd for e in self.entries if e.kind == "session"), 6)

    def labels(self, kind: str) -> list[str]:
        return [e.label for e in self.entries if e.kind == kind]

    def assert_identity(self) -> None:
        """Raise unless the recorded entries are exactly the stages the plan produces.

        Checking the labels, not the counts, is the point: a run that recorded the
        right NUMBER of stages under the wrong names has lost a candidate, and a count
        cannot see that. Comparing sets also covers the duplicate case, because a
        repeated label means one expected stage is missing.
        """
        want_rollouts, want_sessions = expected_labels(self.mode, self.k)
        got_rollouts = {e.label for e in self.entries if e.kind == "rollout"}
        got_sessions = {e.label for e in self.entries if e.kind == "session"}
        if got_rollouts == want_rollouts and got_sessions == want_sessions:
            return
        rl, sl = self.labels("rollout"), self.labels("session")
        detail = [
            f"{self.mode}-{self.k} {self.task_id}: "
            f"R={self.R} (want {len(want_rollouts)}), P={self.P} (want {len(want_sessions)})",
            f"  rollouts: {', '.join(rl) or '(none)'}",
            f"  sessions: {', '.join(sl) or '(none)'}",
        ]
        for kind, want, got in (
            ("rollout", want_rollouts, got_rollouts),
            ("session", want_sessions, got_sessions),
        ):
            missing = sorted(want - got)
            extra = sorted(got - want)
            if missing or extra:
                detail.append(
                    f"  {kind} labels: missing {missing or '(none)'}, "
                    f"unexpected {extra or '(none)'}"
                )
        dupes = sorted(
            {label for label in rl if rl.count(label) > 1}
            | {label for label in sl if sl.count(label) > 1}
        )
        if dupes:
            detail.append(
                f"  DUPLICATE labels: {dupes} -- a repeated label means two stages "
                f"wrote the same run id and one silently overwrote the other, so a "
                f"candidate the ledger counts was never actually produced"
            )
        raise BudgetError("\n".join(detail))

    def to_dict(self) -> dict:
        want_rollouts, want_sessions = expected_labels(self.mode, self.k)
        return {
            "mode": self.mode,
            "task_id": self.task_id,
            "k": self.k,
            "R": self.R,
            "expected_R": len(want_rollouts),
            "P": self.P,
            "session_attempts": sum(
                entry.attempts for entry in self.entries if entry.kind == "session"
            ),
            "expected_P": len(want_sessions),
            "usd": self.usd,
            "rollout_usd": self.rollout_usd,
            "session_usd": self.session_usd,
            "rollouts": self.labels("rollout"),
            "sessions": self.labels("session"),
        }
