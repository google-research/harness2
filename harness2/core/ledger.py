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


def expected_rollouts(k: int) -> int:
    """R = 4k: k shared base + 2k candidates + k finals."""
    return 4 * int(k)


def expected_sessions(k: int) -> int:
    """P = 3k: k INV + k DOM + k compositions (parallel: agg1..aggk; sequential: c1..ck)."""
    return 3 * int(k)


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
        """Raise unless the recorded entries show exactly R=4k and P=3k with no duplicate
        label. Duplicates are checked before the counts, because a duplicate can satisfy them.
        """
        want_r, want_p = expected_rollouts(self.k), expected_sessions(self.k)
        rl, sl = self.labels("rollout"), self.labels("session")
        dupes = sorted(
            {label for label in rl if rl.count(label) > 1}
            | {label for label in sl if sl.count(label) > 1}
        )
        if not dupes and self.R == want_r and self.P == want_p:
            return
        detail = [
            f"{self.mode}-{self.k} {self.task_id}: "
            f"R={self.R} (want {want_r}), P={self.P} (want {want_p})",
            f"  rollouts: {', '.join(self.labels('rollout')) or '(none)'}",
            f"  sessions: {', '.join(self.labels('session')) or '(none)'}",
        ]
        if dupes:
            detail.append(
                f"  DUPLICATE labels: {dupes} -- a repeated label means two stages "
                f"wrote the same run id and one silently overwrote the other, so a "
                f"candidate the ledger counts was never actually produced"
            )
        raise BudgetError("\n".join(detail))

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "task_id": self.task_id,
            "k": self.k,
            "R": self.R,
            "expected_R": expected_rollouts(self.k),
            "P": self.P,
            "session_attempts": sum(
                entry.attempts for entry in self.entries if entry.kind == "session"
            ),
            "expected_P": expected_sessions(self.k),
            "usd": self.usd,
            "rollout_usd": self.rollout_usd,
            "session_usd": self.session_usd,
            "rollouts": self.labels("rollout"),
            "sessions": self.labels("session"),
        }
