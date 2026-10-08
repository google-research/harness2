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

"""The per-domain experience library R: what earlier tasks in this domain produced.

    <root>/<run-tag>/<mode>/<domain>/INDEX.md
    <root>/<run-tag>/<mode>/<domain>/harnesses/<task>/harness/<rel>   the full adopted tree
    <root>/<run-tag>/<mode>/<domain>/harnesses/<task>/EXPERIENCE.md   why picked + lesson

`<mode>` is the on-disk recursion token, `agg` or `seq`. Retrieval is by construction: a
domain's directory only ever holds same-domain entries. The whole tree is carried, never a
summary. Roots are scoped per run and mode, so each sweep starts every domain empty.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import context
from . import tree as harness_tree
from .tree import SurfaceSpec


def slug(task_id: str) -> str:
    return task_id.replace("/", "__")


def has_experience(experience: dict | None) -> bool:
    return isinstance(experience, dict) and all(
        isinstance(experience.get(key), str) and experience[key].strip()
        for key in ("cue", "lesson", "evidence")
    )


def domain_dir(domain: str) -> str:
    """One directory name for a domain, never a path: only separators and the whitespace
    around them collapse, so a domain without one maps to itself.
    """
    return re.sub(r"\s*[/\\]\s*", "__", domain).strip() or "unknown"


@dataclass
class Library:
    """One (run, domain) library: read before each task, appended after."""

    root: Path
    domain: str
    surface: SurfaceSpec

    @property
    def dir(self) -> Path:
        return Path(self.root) / domain_dir(self.domain)

    @property
    def index(self) -> Path:
        return self.dir / "INDEX.md"

    def entry_dir(self, task_id: str) -> Path:
        return self.dir / "harnesses" / slug(task_id)

    def exists(self) -> bool:
        return self.index.is_file()

    def context_dir(self) -> Path | None:
        """The directory handed to an improver session, or None when this domain is empty."""
        return self.dir if self.exists() and any(self.dir.rglob("EXPERIENCE.md")) else None

    def entries(self) -> list[str]:
        d = self.dir / "harnesses"
        return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []

    def tree_of(self, task_id: str) -> harness_tree.Tree:
        return harness_tree.read_tree(self.surface, self.entry_dir(task_id) / "harness")

    def forget(self, task_ids: Sequence[str]) -> None:
        """Drop the entries for these tasks and rebuild INDEX.md; unknown tasks are ignored."""
        removed = False
        for tid in task_ids:
            d = self.entry_dir(tid)
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed = True
        if removed:
            self.rebuild_index()

    def retain(self, task_ids: Sequence[str]) -> None:
        """Keep only the entries preceding the current task in this stream."""
        keep = {slug(task_id) for task_id in task_ids}
        self.forget([entry for entry in self.entries() if entry not in keep])

    def add(
        self,
        task_id: str,
        harness: harness_tree.Tree,
        *,
        why: str = "",
        label: str = "",
        run_id: str = "",
        experience: dict | None = None,
    ) -> Path | None:
        """Store one adopted harness plus why it was adopted, and rebuild INDEX.md from disk.

        The tree is checked for grader verdicts before anything is written.
        """
        if not has_experience(experience):
            return None
        context.assert_tree_has_no_grader_verdicts(harness)
        context.assert_tree_has_no_grader_verdicts({"EXPERIENCE.md": str(experience) + why})
        d = self.entry_dir(task_id)

        shutil.rmtree(d / "harness", ignore_errors=True)
        (d / "harness").mkdir(parents=True, exist_ok=True)
        harness_tree.write_tree(self.surface, harness, d / "harness", stubs=False)

        exp = experience or {}
        lines = [
            f"# {task_id}",
            "",
            f"- adopted: `{label}`" + (f"  (run `{run_id}`)" if run_id else ""),
            f"- components: {_inventory(self.surface, harness)}",
            "",
        ]
        if why.strip():
            lines += ["## Why this was chosen", why.strip(), ""]
        if exp.get("edits"):
            lines += ["## What each edit did", str(exp["edits"]).strip(), ""]
        if exp.get("lesson"):
            lines += ["## Transferable lesson"]
            lines += [
                f"- **{f}**: {str(exp[f]).strip()}"
                for f in ("cue", "lesson", "evidence", "confidence")
                if exp.get(f)
            ]
            lines += [""]
        (d / "EXPERIENCE.md").write_text("\n".join(lines), encoding="utf-8")
        self.rebuild_index()
        return d

    def rebuild_index(self) -> Path:
        """Regenerate INDEX.md from what is on disk."""
        rows = [
            f"# Library — {self.domain}",
            "",
            "Each entry is an earlier task in THIS domain whose harness was chosen, stored "
            "with its FULL component tree and why it was picked. Browse this index, then "
            "open the entries that look relevant to the matter in front of you.",
            "",
        ]
        names = self.entries()
        if not names:
            rows.append("_(empty — this is the first task in this domain)_")
        for name in names:
            ed = self.dir / "harnesses" / name
            cue = _field(ed / "EXPERIENCE.md", "cue")
            tree = harness_tree.read_tree(self.surface, ed / "harness")
            rows += [
                f"## `harnesses/{name}/`",
                f"- harness: `harnesses/{name}/harness/` — {_inventory(self.surface, tree)}",
                f"- why:     `harnesses/{name}/EXPERIENCE.md`",
            ]
            if cue:
                rows.append(f"- cue:     {cue}")
            rows.append("")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index.write_text("\n".join(rows), encoding="utf-8")
        return self.index


def _inventory(surface: SurfaceSpec, tree: harness_tree.Tree) -> str:
    """A one-line census of the components actually in this harness."""
    st: dict[str, int] = {"skills": 0, "sub_agents": 0, "scripts": 0}
    skill_prefix = surface.skill_dir + "/"
    plugin_prefix = None if surface.plugin_dir is None else surface.plugin_dir + "/"
    hooks_file = surface.hooks_file
    for rel in tree:
        if rel.startswith(skill_prefix) and rel.endswith("SKILL.md"):
            st["skills"] += 1
        elif rel.startswith("sub_agents/"):
            st["sub_agents"] += 1
        elif rel.startswith("scripts/"):
            st["scripts"] += 1
        elif plugin_prefix is not None and rel.startswith(plugin_prefix):
            st["plugins"] = st.get("plugins", 0) + 1
        elif rel == hooks_file:
            st["hooks"] = st.get("hooks", 0) + 1
    on = len(harness_tree.always_on_text(surface, tree))
    bits = [f"{v} {k}" for k, v in st.items() if v] or ["always-on edits only"]
    return f"{', '.join(bits)}; {on:,} always-on chars"


def _field(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(f"- **{name}**:"):
            return line.split(":", 1)[1].strip()
    return ""


def root_for(base: Path | str, *, benchmark: str, k: int, variant: str = "", tag: str = "") -> Path:
    """Per-run library root `<base>/<benchmark>/k<k>-<variant>`; an explicit `tag` opts into
    sharing one library across runs.
    """
    if tag:
        return Path(base) / benchmark / tag
    parts = [f"k{int(k)}"] + ([variant] if variant else [])
    return Path(base) / benchmark / "-".join(parts)
