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

"""Answer-key TEXT: the leak that the path and verdict tripwires cannot see.

`leak_globs` keep a benchmark's answer-key files out of model context, and the verdict markers
keep the grader's output out. Neither sees the grading text arriving by another route. The
executor has network access and the benchmarks are public, so a rollout that fetches its own
rubric records it in its trajectory, and that trajectory is the improver's evidence. From there
the criteria can shape an adopted harness, its EXPERIENCE.md, and the domain library that every
later task in the domain reads.

A key is the set of word shingles of the adapter's grading text, minus every shingle of the
material the agent was legitimately given (the task prompt and its source files) and minus
quoted passages, which a rubric lifts from that material. A text carries the key when it
shares passages with at least half of the task's criteria: a fetched answer key carries all of
them, while a source document that some criteria quote unmarked reaches a few. Source documents
are binary on LAB and are deliberately never parsed by host Python, so this breadth test, not
subtraction, is what keeps them from matching.

Matching is verbatim on normalised words. A paraphrase, or a leak of only a few criteria, is not
caught: a clean scan bounds how the criteria can arrive and does not prove their absence.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

SHINGLE = 10
MIN_SHARED = 3
MIN_FRACTION = 0.5

_TOKEN = re.compile(r"[a-z0-9]+(?:[.,'][a-z0-9]+)*")
_ESCAPE = re.compile(r"\\(?:[nrt]|u[0-9a-fA-F]{4})")
_QUOTED = re.compile(r"[\"“”]([^\"“”\n]{1,600})[\"“”]")

Shingle = tuple[str, ...]


def _unescape(m: re.Match) -> str:
    s = m.group(0)
    return chr(int(s[2:], 16)) if s[1] == "u" else " "


def words(text: str) -> tuple[list[str], list[int]]:
    """Lower-cased word tokens and their offsets; JSON escapes are undone first, so a key
    printed inside a JSON tool result still lines up word for word."""
    plain = _ESCAPE.sub(_unescape, text)
    toks: list[str] = []
    starts: list[int] = []
    for m in _TOKEN.finditer(plain.lower()):
        toks.append(m.group(0))
        starts.append(m.start())
    return toks, starts


def shingles(text: str) -> set[Shingle]:
    toks, _ = words(text)
    return {tuple(toks[i : i + SHINGLE]) for i in range(len(toks) - SHINGLE + 1)}


@dataclass(frozen=True)
class Hit:
    """Where a text carries answer-key words. Never the words themselves: the hit is written
    into stage errors and summaries, and those must not become a second route for the key."""

    shared: int
    sources: int
    of: int
    offset: int
    source: str

    def describe(self) -> str:
        return (
            f"{self.shared} answer-key passage(s) of {SHINGLE}+ words from {self.sources} of "
            f"{self.of} criteria, the first from {self.source!r} at offset {self.offset}"
        )


@dataclass(frozen=True)
class AnswerKey:
    """The grading text of one task, as shingles the agent was never shown."""

    task_id: str
    index: Mapping[Shingle, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.index)

    @classmethod
    def build(
        cls, task_id: str, items: Mapping[str, str], visible: Iterable[str] = ()
    ) -> AnswerKey:
        """`items` maps a name for each grading passage to its text; `visible` is everything
        the agent legitimately received. Quoted passages are dropped before shingling."""
        index: dict[Shingle, str] = {}
        for name, text in items.items():
            for sh in shingles(_QUOTED.sub(" ", str(text))):
                index.setdefault(sh, str(name))
        key = cls(task_id, index)
        for text in visible:
            if not index:
                break
            for sh in key._shared(text):
                index.pop(sh, None)
        return key

    def _shared(self, text: str) -> dict[Shingle, int]:
        """Distinct key shingles in `text`, each with the offset of its first occurrence."""
        if not self.index:
            return {}
        toks, starts = words(text)
        first = {sh[0] for sh in self.index}
        found: dict[Shingle, int] = {}
        for i in range(len(toks) - SHINGLE + 1):
            if toks[i] not in first:
                continue
            sh = tuple(toks[i : i + SHINGLE])
            if sh in self.index and sh not in found:
                found[sh] = starts[i]
        return found

    def find(self, text: str) -> Hit | None:
        """A Hit when `text` shares MIN_SHARED key shingles drawn from at least MIN_FRACTION
        of the key's criteria, else None.

        The breadth test is what separates a fetched answer key, which carries every criterion,
        from source documents a few criteria quote without quotation marks.
        """
        found = self._shared(text)
        if len(found) < MIN_SHARED:
            return None
        of = len(set(self.index.values()))
        hit = {self.index[sh] for sh in found}
        if len(hit) < max(1, math.ceil(MIN_FRACTION * of)):
            return None
        sh, offset = min(found.items(), key=lambda kv: kv[1])
        return Hit(shared=len(found), sources=len(hit), of=of, offset=offset, source=self.index[sh])


__all__ = ["AnswerKey", "Hit", "MIN_FRACTION", "MIN_SHARED", "SHINGLE", "shingles", "words"]
