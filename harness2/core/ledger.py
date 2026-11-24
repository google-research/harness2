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

from collections.abc import Mapping
from dataclasses import dataclass, field


def expected_rollouts(k: int) -> int:
    """R = 4k: k shared base + 2k candidates + k finals."""
    return 4 * int(k)


def expected_sessions(k: int) -> int:
    """P = 3k: k INV + k DOM + k compositions (parallel: agg1..aggk; sequential: c1..ck)."""
    return 3 * int(k)


def persisted_violation(ledger: Mapping | None, *, k: int | None = None) -> str:
    """Check a persisted `Ledger.to_dict()` mapping; "" when valid or absent (legacy summary)."""
    if ledger is None:
        return ""
    if not isinstance(ledger, Mapping):
        return f"invalid ledger record ({type(ledger).__name__})"
    raw_k = k if k is not None else ledger.get("k")
    scale = raw_k if isinstance(raw_k, int) and not isinstance(raw_k, bool) and raw_k > 0 else None
    want_r = ledger.get("expected_R", expected_rollouts(scale) if scale is not None else None)
    want_p = ledger.get("expected_P", expected_sessions(scale) if scale is not None else None)
    got_r = ledger.get("R")
    got_p = ledger.get("P")
    if not all(
        isinstance(v, int) and not isinstance(v, bool) for v in (got_r, got_p, want_r, want_p)
    ):
        return f"R={got_r}/{want_r}, P={got_p}/{want_p}"

    problems: list[str] = []
    if got_r != want_r or got_p != want_p:
        problems.append(f"R={got_r}/{want_r}, P={got_p}/{want_p}")

    rl = ledger.get("rollouts")
    sl = ledger.get("sessions")
    if rl is not None and (not isinstance(rl, list) or len(rl) != got_r):
        n_rl = len(rl) if isinstance(rl, list) else type(rl).__name__
        problems.append(f"rollouts list ({n_rl}) != R={got_r}")
    if sl is not None and (not isinstance(sl, list) or len(sl) != got_p):
        n_sl = len(sl) if isinstance(sl, list) else type(sl).__name__
        problems.append(f"sessions list ({n_sl}) != P={got_p}")

    dupes = sorted(
        ({str(x) for x in rl if rl.count(x) > 1} if isinstance(rl, list) else set())
        | ({str(x) for x in sl if sl.count(x) > 1} if isinstance(sl, list) else set())
    )
    if dupes:
        problems.append(f"duplicate labels {dupes}")
    return "; ".join(problems)


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
