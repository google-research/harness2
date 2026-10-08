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

"""The evidence tree an agentic proposer reads. The only way evidence reaches a model.

    context/task.md                  the task as the agent received it
    context/rollouts/<label>.md      full trajectory -- no prompt-budget truncation
    context/deliverables/<label>.md  the artifact, via the judge's own extractor
    context/harness/<rel>            the c1..c8 tree being edited
    context/library/INDEX.md         earlier tasks in this domain + their full harnesses
    context/pool/<label>/            candidate harness + its rollout (aggregate / select only)
    MISSION.md                       what to do with all of it

Evidence is served as files and the prompt is a short pointer, so nothing is truncated to fit
a prompt. `mission()` builds that pointer.
"""

from __future__ import annotations

import difflib
import fnmatch
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from . import tree as harness_tree
from .spec import BenchmarkAdapter, RunView


@dataclass
class ContextTree:
    """A built evidence tree, plus what actually landed in it."""

    root: Path
    task_id: str
    rollouts: list[str]
    deliverables: list[str]
    harness_files: int
    library: bool
    pool: list[str]
    failed: list[str] = field(default_factory=list)

    deliverables_mode: str = "contents"
    source_files: int = 0
    decisions: int = 0

    @property
    def layout(self) -> str:
        """The evidence-layout listing that goes in the mission; only mentions what exists."""
        lines = ["context/task.md                  the task as the agent received it"]
        if self.source_files:
            lines.append("context/source/                 task source materials")
        if self.decisions:
            lines.append("context/decisions/              earlier edits and composition decisions")
        if self.rollouts:
            names = ", ".join(f"{r}.md" for r in self.rollouts[:6])
            more = f" (+{len(self.rollouts) - 6} more)" if len(self.rollouts) > 6 else ""
            lines.append(f"context/rollouts/               FULL trajectories: {names}{more}")
        if self.deliverables:
            if self.deliverables_mode == "names":
                lines.append(
                    "context/deliverables/<label>/    INDEX.md lists WHICH files each "
                    "rollout produced -- names only."
                )
                lines.append(
                    "                                 File contents are deliberately "
                    "not provided on this benchmark; work from the trajectories and "
                    "the file set itself."
                )
            else:
                lines.append(
                    "context/deliverables/<label>/    the files each rollout produced, "
                    "one per artifact (browse INDEX.md for the set)"
                )
        if self.harness_files:
            lines.append(
                f"context/harness/                the harness you are editing "
                f"({self.harness_files} files)"
            )
        if self.pool:
            lines.append(
                f"context/pool/                   {len(self.pool)} candidates, each "
                f"with its harness and its rollout"
            )
        if self.library:
            lines.append(
                "context/library/                earlier tasks in this "
                "area -- browse INDEX.md first, then open what looks relevant"
            )
        return "\n".join(lines)


def build(
    dest: Path | str,
    adapter: BenchmarkAdapter,
    task_id: str,
    rollouts: Sequence[RunView] = (),
    *,
    harness_dir: Path | None = None,
    library_dir: Path | None = None,
    pool: Sequence[tuple[str, Path, RunView | None]] = (),
    decisions: Mapping[str, str] | None = None,
    obs_trunc: int | None = None,
) -> ContextTree:
    """Write the evidence tree; return what landed, so the mission describes only that.

    `obs_trunc` applies per observation, but a trajectory as a whole is written in full.
    Runs inside `recursion._run_stage`'s failure domain, so an adapter bug raised here is
    recorded as a stage error; only the trajectory and deliverable readers are caught,
    because their failure is itself evidence.
    """
    dest = Path(dest)

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    trunc = obs_trunc if obs_trunc is not None else adapter.spec.obs_trunc

    (dest / "task.md").write_text(adapter.task_prompt(task_id), encoding="utf-8")
    source_files = copy_source(adapter, task_id, dest)
    for label, notes in (decisions or {}).items():
        path = dest / "decisions" / f"{label}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(notes or "No decision notes returned.", encoding="utf-8")

    got_r: list[str] = []
    got_d: list[str] = []
    failed_r: list[str] = []
    if rollouts:
        (dest / "rollouts").mkdir(exist_ok=True)
        (dest / "deliverables").mkdir(exist_ok=True)
    names_only = adapter.spec.proposer_deliverables == "names"
    for r in rollouts:
        failure = _write_rollout(dest, r, adapter, trunc, names_only)
        if failure:
            failed_r.append(failure)
        else:
            got_r.append(r.label)
        got_d.append(r.label)

    n_h = 0
    if harness_dir and Path(harness_dir).is_dir():
        assert_no_leak_paths(harness_dir, adapter.spec.leak_globs)
        n_h = _copy_tree(Path(harness_dir), dest / "harness")

    got_p = _write_pool(dest, pool, adapter, trunc, names_only)

    lib = False
    if library_dir and Path(library_dir).is_dir() and any(Path(library_dir).iterdir()):
        assert_no_leak_paths(library_dir, adapter.spec.leak_globs)

        shutil.copytree(library_dir, dest / "library", dirs_exist_ok=True)
        lib = True

    assert_no_leak_paths(dest, adapter.spec.leak_globs)

    assert_no_grader_verdicts(dest)

    if rollouts and not got_r:
        raise RuntimeError(
            "no trajectory could be rendered for ANY rollout -- the proposer would be adapting "
            "on nothing: " + "; ".join(failed_r[:4])
        )
    return ContextTree(
        root=dest,
        task_id=task_id,
        rollouts=got_r,
        deliverables=got_d,
        harness_files=n_h,
        library=lib,
        pool=got_p,
        failed=failed_r,
        deliverables_mode=("names" if names_only else "contents"),
        source_files=source_files,
        decisions=len(decisions or {}),
    )


def copy_source(adapter: BenchmarkAdapter, task_id: str, root: Path) -> int:
    """Copy the task's source material under `root`, minus answer-key paths."""
    src_fn = getattr(adapter, "task_files", None)
    if not callable(src_fn):
        return 0
    src = src_fn(task_id)
    if not src or not Path(src).is_dir():
        return 0
    dst = root / "source"
    src_n = 0
    for f in sorted(Path(src).rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts:
            continue
        rel = f.relative_to(src).as_posix()
        if any(
            fnmatch.fnmatch(rel, g.lstrip("./")) or fnmatch.fnmatch(rel, f"*/{g.lstrip('./')}")
            for g in adapter.spec.leak_globs
        ):
            continue
        t = dst / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, t)
        src_n += 1
    if src_n:
        assert_no_leak_paths(dst, adapter.spec.leak_globs)
    return src_n


def _write_rollout(dest: Path, r: RunView, adapter, trunc: int, names_only: bool) -> str:
    """Write one rollout's trajectory and deliverables; return "" or the failure detail."""
    failure = ""
    try:
        (dest / "rollouts" / f"{r.label}.md").write_text(
            adapter.trajectory(r.run_dir, obs_trunc=trunc), encoding="utf-8"
        )
    except Exception as exc:
        (dest / "rollouts" / f"{r.label}.md").write_text(
            f"(trajectory unavailable: {type(exc).__name__}: {exc})", encoding="utf-8"
        )
        failure = f"{r.label}: {type(exc).__name__}: {exc}"

    per_file: dict[str, str] = {}
    if names_only:
        _write_listing(dest / "deliverables" / r.label, r.label, _listing(adapter, r.run_dir))
    else:
        files_fn = getattr(adapter, "deliverable_files", None)
        if callable(files_fn):
            per_file = dict(files_fn(r.run_dir) or {})
    if not per_file:
        if not names_only:
            try:
                text = adapter.deliverable_text(r.run_dir) or "(no deliverable produced)"
            except Exception as exc:
                text = f"(deliverable unavailable: {type(exc).__name__}: {exc})"
            (dest / "deliverables" / f"{r.label}.md").write_text(text, encoding="utf-8")
        return failure

    d = dest / "deliverables" / r.label
    d.mkdir(parents=True, exist_ok=True)
    for img in _images(adapter, r.run_dir)[:8]:
        try:
            t = d / "images" / Path(img).name
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(img, t)
        except OSError:
            pass
    for name, body in per_file.items():
        safe = str(name).replace("/", "__") or "unnamed"
        suffix = "" if safe.endswith(".txt") else ".txt"
        (d / f"{safe}{suffix}").write_text(str(body), encoding="utf-8")
    raw_fn = getattr(adapter, "deliverable_assets", None)
    raw_names: list[str] = []
    for src in raw_fn(r.run_dir) if callable(raw_fn) else []:
        t = d / "raw" / Path(src).name
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, t)
        raw_names.append(Path(src).name)
    raw_note = (
        "\n\n`raw/` holds the binary originals of the artifacts above (same names). "
        "The rendered\n`.txt` view is CANONICAL -- it reads each artifact the way the "
        "grader does (a formula\nwith no cached value renders as the empty cell the "
        "grader sees, even though the raw\nworkbook computes it on open). Open a raw "
        "file only for detail the rendering flattened,\nnever to overrule it.\n"
        if raw_names
        else ""
    )
    (d / "INDEX.md").write_text(
        f"# {r.label} produced {len(per_file)} file(s)\n\n"
        + "\n".join(f"- `{n}`  ({len(str(b)):,} chars extracted)" for n, b in per_file.items())
        + "\n\nEach `.txt` is the artifact converted to text by the SAME reader the "
        "work\nis assessed through, so reason about correctness from these -- an "
        "artifact\nthat does not survive that conversion is treated as unreadable "
        "however good\nit looks. `images/` holds any images from the same set, which "
        "are also part\nof how the work is read.\n" + raw_note,
        encoding="utf-8",
    )
    return failure


def _write_pool(
    dest: Path,
    pool: Sequence[tuple[str, Path, RunView | None]],
    adapter,
    trunc: int,
    names_only: bool,
) -> list[str]:
    """Write each pool candidate's harness and rollout; return the labels written."""
    got: list[str] = []
    for label, cand_dir, roll in pool:
        d = dest / "pool" / label
        d.mkdir(parents=True, exist_ok=True)
        if cand_dir and Path(cand_dir).is_dir():
            assert_no_leak_paths(cand_dir, adapter.spec.leak_globs)
            _copy_tree(Path(cand_dir), d / "harness")
            surface = adapter.substrate.surface()
            reference = harness_tree.read_tree(surface, dest / "harness")
            candidate = harness_tree.read_tree(surface, cand_dir)
            diff = "".join(
                line
                for name in sorted(reference.keys() | candidate.keys())
                for line in difflib.unified_diff(
                    reference.get(name, "").splitlines(keepends=True),
                    candidate.get(name, "").splitlines(keepends=True),
                    fromfile=f"current/{name}",
                    tofile=f"{label}/{name}",
                )
            )
            (d / "harness.diff").write_text(diff, encoding="utf-8")
        if roll is not None:
            try:
                (d / "rollout.md").write_text(
                    adapter.trajectory(roll.run_dir, obs_trunc=trunc), encoding="utf-8"
                )

                produced = (
                    _listing_md(label, _listing(adapter, roll.run_dir))
                    if names_only
                    else adapter.deliverable_text(roll.run_dir) or "(nothing produced)"
                )
                (d / "produced.md").write_text(produced, encoding="utf-8")
            except Exception as exc:
                (d / "rollout.md").write_text(f"(unavailable: {exc})", encoding="utf-8")
        got.append(label)
    return got


def _listing(adapter, run_dir: Path) -> list[str]:
    """Deliverable names for one rollout, from the adapter, never a directory scan."""
    fn = getattr(adapter, "deliverable_listing", None)
    if not callable(fn):
        return [
            "(this adapter declares proposer_deliverables='names' but implements no "
            "deliverable_listing -- nothing can be listed)"
        ]
    try:
        return [str(x) for x in (fn(run_dir) or [])]
    except Exception as exc:
        return [f"(listing unavailable: {type(exc).__name__}: {exc})"]


_WITHHELD_NOTE = (
    "CONTENTS ARE WITHHELD ON THIS PATH, DELIBERATELY. You are shown WHICH files the rollout\n"
    "produced and not what is inside them. This is not an oversight and not a size budget:\n"
    "on this benchmark the extracted-deliverable tree lives inside the same trial directory as\n"
    "the grader's own output, so serving file bodies here once served the grader's pass rate\n"
    "and failed-check names along with them. Reason about the work from the TRAJECTORY -- what\n"
    "was attempted, what was observed, what the run did with what it saw -- and from the file\n"
    "set itself, which is evidence in its own right: the task names the artifacts it expects,\n"
    "so a missing or misnamed file is a finding. Do not ask for the contents and do not infer\n"
    "that an unlisted file is empty.\n"
)


def _listing_md(label: str, names: list[str]) -> str:
    """One line naming the label and its files, plus the withheld-contents note."""
    return (
        f"# {label} produced {len(names)} file(s)\n\n"
        f"{label}: {names!r}\n\n" + "\n".join(f"- `{n}`" for n in names) + "\n\n" + _WITHHELD_NOTE
    )


def _write_listing(d: Path, label: str, names: list[str]) -> None:
    """Write INDEX.md and nothing else: under names mode there is no per-artifact content."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "INDEX.md").write_text(_listing_md(label, names), encoding="utf-8")


def _images(adapter, run_dir: Path) -> list[Path]:
    """Images the benchmark's own reader would pick up from this artifact set."""
    fn = getattr(adapter, "deliverable_images", None)
    if not callable(fn):
        return []
    return [Path(p) for p in (fn(run_dir) or [])]


def _copy_tree(src: Path, dst: Path) -> int:
    """Copy a component tree, dropping byte-compiled files; return the file count."""
    n = 0
    for p in sorted(src.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        t = dst / p.relative_to(src)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, t)
        n += 1
    return n


def assert_no_leak_paths(root: Path | str, patterns: Sequence[str]) -> None:
    """Reject benchmark-declared answer-key paths before a context reaches a model."""
    root = Path(root)
    if not patterns or not root.is_dir():
        return
    hits: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        for pattern in patterns:
            pat = str(pattern).replace("\\", "/").lstrip("./")
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, f"*/{pat}"):
                hits.append(rel)
                break
    if hits:
        shown = ", ".join(sorted(hits)[:8])
        raise ValueError(f"answer-key path(s) would enter model context: {shown}")


GRADER_VERDICT_MARKERS: tuple[str, ...] = (
    "deterministic_eval_summary",
    "deterministic_eval_result",
    "judge_scope_and_deterministic_status",
    "Judge findings on the rendered result",
    '"passed_count"',
    '"pass_rate"',
    '"test_pass_rate"',
)

_MARKER_SCAN_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".json",
        ".csv",
        ".tsv",
        ".yaml",
        ".yml",
        ".html",
        ".htm",
        ".log",
        ".py",
        ".js",
        ".sh",
        ".xml",
        ".ini",
        ".toml",
        ".patch",
        ".diff",
        "",
    }
)


def assert_no_grader_verdicts(
    root: Path | str, markers: Sequence[str] = GRADER_VERDICT_MARKERS
) -> None:
    """Refuse a context tree containing the grader's own output, by file name and by content.

    A tripwire on the adapters' read boundary, not the defence itself: if it fires, find the
    read path that produced the match rather than widening the marker list.
    """
    root = Path(root)
    if not markers or not root.is_dir():
        return
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        for m in markers:
            if m in rel:
                raise ValueError(
                    f"grader verdict would enter model context: {rel!r} matched marker {m!r} "
                    f"in its NAME. The tripwire in core.context fired -- this is a leak, not "
                    f"a false positive to widen around. Find the read path that produced it."
                )
        if p.suffix.lower() not in _MARKER_SCAN_SUFFIXES:
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in markers:
            if m in body:
                where = body.index(m)
                raise ValueError(
                    f"grader verdict would enter model context: {rel!r} contains marker {m!r} "
                    f"at offset {where} ({body[max(0, where - 60) : where + 120]!r}). The "
                    f"tripwire in core.context fired -- this is a leak, not a false positive "
                    f"to widen around. Find the read path that produced it."
                )


def assert_tree_has_no_grader_verdicts(
    tree: Mapping[str, str], markers: Sequence[str] = GRADER_VERDICT_MARKERS
) -> None:
    """The same tripwire on an agent-authored harness tree (rel -> body), before it can be
    rolled or stored: such a tree could never afterwards be served as evidence.
    """
    for rel in sorted(tree):
        for m in markers:
            if m in rel:
                raise ValueError(
                    f"harness file {rel!r} has grader-verdict marker {m!r} in its NAME; rename "
                    f"it -- files carrying these strings cannot be served as evidence to later "
                    f"tasks"
                )
        if Path(rel).suffix.lower() not in _MARKER_SCAN_SUFFIXES:
            continue
        body = str(tree[rel])
        for m in markers:
            if m in body:
                where = body.index(m)
                raise ValueError(
                    f"harness file {rel!r} contains grader-verdict marker {m!r} at offset "
                    f"{where} ({body[max(0, where - 60) : where + 120]!r}); remove or rename "
                    f"that string -- files carrying it cannot be served as evidence to later "
                    f"tasks"
                )


def mission(tree: ContextTree, *, job: str) -> str:
    """The short pointer to the evidence on disk: the job, where the evidence is, and the
    instruction to open it.
    """
    parts = [
        job.strip(),
        "",
        "# YOUR EVIDENCE",
        "",
        "It is on disk, in `context/`. Read it -- do not answer from this message alone.",
        "",
        tree.layout,
        "",
        "Every trajectory EVENT is present: the trajectory was not clipped to fit this "
        "mission. An individual observation may contain an explicit renderer-truncation "
        "marker; that marker is ours, not behavior by the solver. Do not diagnose it as a "
        "solver failure.",
        "",
        "Read `task.md` and the current harness first. Inventory every available rollout "
        "or candidate and inspect each at least once before deciding. For a candidate, "
        "compare what changed from its editing baseline, what behavior changed in its "
        "rollout, what improved, and what regressed or remained unresolved. Then "
        "deep-read the evidence relevant to any edit. Cite the specific trajectory "
        "moment behind every change; one you cannot trace to a moment is speculation.",
    ]
    return "\n".join(parts)
