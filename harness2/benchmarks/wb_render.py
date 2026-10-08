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

"""The renderer for what a WB rollout PRODUCED: agent-authored bytes in, text out.

`_admit()` is a positive allowlist of four locations, one per subset, and raises on
anything else. `render`/`flat_text` are the selector's view (contents), `listing` the
proposer's (names only). Binary handlers use the configured renderer interpreter when the
local interpreter lacks openpyxl / python-pptx, and fail loudly rather than degrade a
workbook to a stub.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ALLOWLIST: dict[str, tuple[str, ...]] = {
    "office": ("verifier/raw_artifacts/**", "verifier/agent.patch"),
    "web": ("verifier/agent.workspace.tar.gz",),
    "code": ("verifier/agent.patch",),
    "sec": ("artifacts/logs/artifacts/**",),
}


FORBIDDEN: tuple[str, ...] = (
    "verifier/artifact_text/**",
    "verifier/artifact_manifest.json",
    "verifier/evaluator_artifact_manifest.json",
    "verifier/score.json",
    "verifier/reward.json",
    "verifier/reward.txt",
    "verifier/results.xml",
    "verifier/test_output.txt",
    "verifier/eval_result.json",
    "verifier/evidence/**",
    "verifier/judge_evidence/**",
)


class RenderInputError(RuntimeError):
    """A path outside the subset's allowlist was offered to the renderer."""


class RenderBackendError(RuntimeError):
    """A binary artifact could not be rendered by either the local interpreter or the bridge."""


def _admit(trial: Path, path: Path, subset: str) -> str:
    """Return the trial-relative path, or raise. Every read in this module goes through here."""
    pats = ALLOWLIST.get(subset)
    if not pats:
        raise RenderInputError(
            f"no input allowlist for subset {subset!r}; known: {sorted(ALLOWLIST)}"
        )
    try:
        rel = path.resolve().relative_to(trial.resolve()).as_posix()
    except ValueError as exc:
        raise RenderInputError(f"{path} is not inside the trial dir {trial}") from exc
    for pat in pats:
        if pat.endswith("/**"):
            head = pat[:-3]
            if rel == head or rel.startswith(head + "/"):
                return rel
        elif rel == pat:
            return rel
    raise RenderInputError(
        f"wb_render refuses to open {rel!r} for subset {subset!r}: the allowlist is "
        f"{list(pats)}. Everything else in a WB trial directory is grader output written by the "
        f"verifier stage of the same trial, and reading it is the leak this module replaces."
    )


def _read_bytes(trial: Path, path: Path, subset: str) -> bytes:
    _admit(trial, path, subset)
    return path.read_bytes()


_PROVENANCE_HEAD = (
    "verifier-owned",
    "verifier-extracted",
    "verifier-generated",
    "for the judge",
    "judge rubric",
    "judge modality",
    "deterministic verifier status",
)
_PROVENANCE_HEAD_CHARS = 4000

_VERDICT_ANY = (
    "deterministic checks:",
    "pass_rate",
    "passed_count",
    "failed_checks",
    "test_pass_rate",
)

_PROVENANCE_NAME_PREFIXES = ("judge_evidence",)


def _is_verifier_authored(name: str, text: str | None, subset: str = "office") -> str | None:
    """Reason string if this admitted file looks verifier-authored, else None; the scan
    runs only on the verifier-populated subsets (office, sec)."""
    if subset in ("code", "web"):
        return None
    low_name = name.lower()
    for pre in _PROVENANCE_NAME_PREFIXES:
        if low_name.startswith(pre):
            return f"filename begins with {pre!r}, the verifier's evidence naming convention"
    if text is None:
        return None
    head = text[:_PROVENANCE_HEAD_CHARS].lower()
    for phrase in _PROVENANCE_HEAD:
        if phrase in head:
            return f"file head declares verifier provenance ({phrase!r})"
    low = text.lower()
    for phrase in _VERDICT_ANY:
        if phrase in low:
            return f"contains a grader verdict marker ({phrase!r})"
    return None


TEXT_SUFFIXES = frozenset(
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
    }
)
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _image_stub(name: str, size: int) -> str:
    return (
        f"(image, {size:,} bytes -- not inlined)\n"
        f"Images are named and sized, never rendered into text: a base64 blob or an OCR guess "
        f"in an evidence file is noise to a reader and invites reasoning about pixels nobody "
        f"actually looked at."
    )


def _binary_stub(name: str, size: int) -> str:
    return f"({Path(name).suffix or 'no-extension'} file, {size:,} bytes -- binary, not rendered)"


_XLSX_NO_CACHE = "(no cached value; a data_only reader sees this cell as EMPTY)"


def _xlsx_text(path: Path, display_name: str) -> str:
    """Both facets of every cell: openpyxl writes a formula as text and never evaluates
    it, so a workbook no spreadsheet application has opened shows those cells as EMPTY
    to a plain reader."""
    import openpyxl

    wb_f = openpyxl.load_workbook(path, data_only=False)
    wb_v = openpyxl.load_workbook(path, data_only=True)
    out: list[str] = [f"# Workbook: {display_name}"]
    for idx, ws_f in enumerate(wb_f.worksheets, start=1):
        ws_v = wb_v[ws_f.title] if ws_f.title in wb_v.sheetnames else None
        out.append("")
        out.append(f"## Sheet {idx}: {ws_f.title}")
        out.append(f"Dimensions: {ws_f.max_row} rows x {ws_f.max_column} columns")
        rows_v = list(ws_v.iter_rows(values_only=True)) if ws_v is not None else []
        for r, row_f in enumerate(ws_f.iter_rows(values_only=True), start=1):
            row_v = rows_v[r - 1] if r - 1 < len(rows_v) else ()
            cells: list[str] = []
            for c, raw in enumerate(row_f):
                cached = row_v[c] if c < len(row_v) else None
                cells.append(_xlsx_cell(raw, cached))
            if not any(x.strip() for x in cells):
                continue
            out.append(f"R{r}: " + " | ".join(cells))
    return "\n".join(out)


def _xlsx_cell(raw, cached) -> str:
    if raw is None:
        return ""
    s = raw if isinstance(raw, str) else str(raw)
    if not isinstance(raw, str):
        if type(raw).__name__ in ("ArrayFormula", "DataTableFormula"):
            s = getattr(raw, "text", None) or str(raw)
        else:
            return s
    if not s.startswith("="):
        return s
    if cached is None or (isinstance(cached, str) and cached == ""):
        return f"formula:{s} -> {_XLSX_NO_CACHE}"
    return f"formula:{s} = {cached}"


def _pptx_text(path: Path, display_name: str) -> str:
    from pptx import Presentation

    prs = Presentation(str(path))
    out: list[str] = [f"# Deck: {display_name}", f"Slides: {len(prs.slides)}"]
    for n, slide in enumerate(prs.slides, start=1):
        out.append("")
        out.append(f"## slide {n}")
        wrote = False
        for shape in slide.shapes:
            for line in _shape_lines(shape):
                out.append(line)
                wrote = True
        if not wrote:
            out.append("(no text frames on this slide)")
    return "\n".join(out)


def _shape_lines(shape) -> list[str]:
    """Text of one shape, recursing into groups and tables. Empty frames produce nothing."""
    lines: list[str] = []
    try:
        if getattr(shape, "shape_type", None) is not None and hasattr(shape, "shapes"):
            for sub in shape.shapes:
                lines += _shape_lines(sub)
            return lines
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                lines.append(" | ".join(c.text for c in row.cells))
            return lines
        if getattr(shape, "has_text_frame", False):
            body = shape.text_frame.text
            if body.strip():
                lines.append(body)
    except Exception as exc:
        lines.append(f"(shape unreadable: {type(exc).__name__}: {exc})")
    return lines


_HAVE: dict[str, bool] = {}


def _have(mod: str) -> bool:
    if mod not in _HAVE:
        try:
            __import__(mod)
            _HAVE[mod] = True
        except ImportError:
            _HAVE[mod] = False
    return _HAVE[mod]


def _bridge_interpreter() -> str:
    """The renderer interpreter; empty when invoked as the bridge itself."""
    try:
        from .. import config
    except ImportError:
        return ""
    return str(config.WB_RENDER_PY)


_BINARY_HANDLERS = {".xlsx": ("openpyxl", _xlsx_text), ".pptx": ("pptx", _pptx_text)}


def _render_binaries(
    trial: Path, subset: str, items: list[tuple[str, Path, str]]
) -> dict[str, str]:
    """{key -> text} for every xlsx/pptx in `items`, locally or through ONE bridge call."""
    if not items:
        return {}
    local: list[tuple[str, Path, str]] = []
    remote: list[tuple[str, Path, str]] = []
    for key, path, name in items:
        mod, _ = _BINARY_HANDLERS[path.suffix.lower()]
        (local if _have(mod) else remote).append((key, path, name))

    out: dict[str, str] = {}
    for key, path, name in local:
        _, fn = _BINARY_HANDLERS[path.suffix.lower()]
        out[key] = fn(path, name)

    if remote:
        payload = {
            "func": "binaries",
            "trial": str(trial),
            "subset": subset,
            "items": [{"key": k, "path": str(p), "name": n} for k, p, n in remote],
        }
        out.update(_bridge(payload)["texts"])
    return out


def _bridge(payload: dict) -> dict:
    bridge_py = _bridge_interpreter()
    if not bridge_py or not Path(bridge_py).is_file():
        raise RenderBackendError(
            f"openpyxl/python-pptx are missing from {sys.executable} and the bridge interpreter "
            f"{bridge_py!r} does not exist. Run uv pip install -e '.[render]' "
            f"in your Harness² environment. "
            f"Refusing to degrade a workbook to '(binary, not rendered)': that is "
            f"indistinguishable from a rollout that produced nothing, and it would silently "
            f"blank the evidence for the whole office subset."
        )
    p = subprocess.run(
        [bridge_py, os.path.abspath(__file__), "--bridge"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if p.returncode != 0:
        raise RenderBackendError(
            f"wb_render bridge ({bridge_py}) failed: {p.stderr.strip()[-1200:]}"
        )
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise RenderBackendError(f"wb_render bridge returned non-JSON: {p.stdout[:400]!r}") from exc


def render(trial_dir: Path | str, subset: str) -> dict[str, str]:
    """{filename -> rendered text} for one rollout. Agent-authored bytes only."""
    files, _ = render_report(trial_dir, subset)
    return files


def render_report(trial_dir: Path | str, subset: str) -> tuple[dict[str, str], list[str]]:
    """`render` plus the quarantine log: (files, ["<name>: <reason>", ...]). Withheld
    files are dropped, not stubbed."""
    trial = Path(trial_dir)
    entries = _entries(trial, subset)
    out: dict[str, str] = {}
    withheld: list[str] = []
    for name, _size, body, scanned in entries:
        reason = _is_verifier_authored(Path(name).name, body if scanned else None, subset)
        if reason:
            withheld.append(f"{name}: {reason}")
            continue
        out[name] = body
    return out, withheld


def flat_text(trial_dir: Path | str, subset: str) -> str:
    """The same rendering as one string, untrimmed."""
    trial = Path(trial_dir)
    entries = [
        e
        for e in _entries(trial, subset)
        if not _is_verifier_authored(Path(e[0]).name, e[2] if e[3] else None, subset)
    ]
    if not entries:
        return ""
    parts = ["## Files produced\n" + "\n".join(f"- {n} ({s:,} bytes)" for n, s, _b, _x in entries)]
    parts += [f"## {n}\n{b}" for n, _s, b, _x in entries]
    return "\n\n".join(parts)


def body_chars(trial_dir: Path | str, subset: str) -> int:
    """Rendered artifact BODY characters, excluding flat_text's "## Files produced" index."""
    trial = Path(trial_dir)
    return sum(
        len(body.strip())
        for name, _size, body, scanned in _entries(trial, subset)
        if not _is_verifier_authored(Path(name).name, body if scanned else None, subset)
    )


def raw_files(trial_dir: Path | str, subset: str) -> list[tuple[str, Path]]:
    """Quarantine-passing RAW binary artifacts: [(display name, path)], office and sec only.

    They ride beside the canonical rendered text; the quarantine decision is the
    rendering's, one per name."""
    trial = Path(trial_dir)
    roots = {
        "office": trial / "verifier" / "raw_artifacts",
        "sec": trial / "artifacts" / "logs" / "artifacts",
    }
    root = roots.get(subset)
    if root is None or not root.is_dir():
        return []
    _admit(trial, root, subset)
    withheld = {
        name
        for name, _s, body, scanned in _entries(trial, subset)
        if _is_verifier_authored(Path(name).name, body if scanned else None, subset)
    }
    out: list[tuple[str, Path]] = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in _BINARY_HANDLERS:
            continue
        _admit(trial, f, subset)
        rel = f.relative_to(root).as_posix()
        if rel in withheld or _is_verifier_authored(Path(rel).name, None, subset):
            continue
        out.append((rel, f))
    return out


def listing(trial_dir: Path | str, subset: str) -> list[str]:
    """The deliverable NAMES only: the PROPOSER's view. Names come from agent-authored
    sources only -- patch headers on office and code, tar members on web, paths on sec."""
    trial = Path(trial_dir)
    if subset in ("office", "code"):
        patch = trial / "verifier" / "agent.patch"
        if not patch.is_file():
            return []
        body = _text(_read_bytes(trial, patch, subset))
        names = {
            ln.split(" b/", 1)[1].strip()
            for ln in body.splitlines()
            if ln.startswith("diff --git ") and " b/" in ln
        }
        return sorted(names)
    if subset == "web":
        tb = trial / "verifier" / "agent.workspace.tar.gz"
        if not tb.is_file():
            return []
        _admit(trial, tb, subset)
        try:
            with tarfile.open(tb, "r:gz") as tf:
                return sorted(f"{m.name} ({m.size:,} bytes)" for m in tf.getmembers() if m.isfile())
        except (tarfile.TarError, OSError) as exc:
            raise RenderBackendError(f"web workspace tarball unreadable: {tb}: {exc}") from exc
    if subset == "sec":
        root = trial / "artifacts" / "logs" / "artifacts"
        if not root.is_dir():
            return []
        _admit(trial, root, subset)
        return sorted(
            f"{f.relative_to(root).as_posix()} ({f.stat().st_size:,} bytes)"
            for f in root.rglob("*")
            if f.is_file()
        )
    raise RenderInputError(f"wb_render has no listing for subset {subset!r}")


def _entries(trial: Path, subset: str) -> list[tuple[str, int, str, bool]]:
    """[(name, byte size, rendered text, was-scannable-as-text)] in stable name order."""
    if subset == "office":
        return _entries_dir(trial, subset, trial / "verifier" / "raw_artifacts")
    if subset == "sec":
        return _entries_dir(trial, subset, trial / "artifacts" / "logs" / "artifacts")
    if subset == "web":
        return _entries_tar(trial, subset, trial / "verifier" / "agent.workspace.tar.gz")
    if subset == "code":
        return _entries_patch(trial, subset, trial / "verifier" / "agent.patch")
    raise RenderInputError(f"wb_render has no handler for subset {subset!r}")


def _entries_dir(trial: Path, subset: str, root: Path) -> list[tuple[str, int, str, bool]]:
    if not root.is_dir():
        return []
    _admit(trial, root, subset)
    plain: list[tuple[str, int, str, bool]] = []
    binaries: list[tuple[str, Path, str]] = []
    sizes: dict[str, int] = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        data = _read_bytes(trial, f, subset)
        sizes[rel] = len(data)
        suf = f.suffix.lower()
        if suf in IMAGE_SUFFIXES:
            plain.append((rel, len(data), _image_stub(rel, len(data)), False))
        elif suf in TEXT_SUFFIXES:
            plain.append((rel, len(data), _text(data), True))
        elif suf in _BINARY_HANDLERS:
            binaries.append((rel, f, f.name))
        else:
            plain.append((rel, len(data), _binary_stub(rel, len(data)), False))
    rendered = _render_binaries(trial, subset, binaries)
    for rel, _f, _n in binaries:
        plain.append((rel, sizes[rel], rendered[rel], True))
    return sorted(plain, key=lambda e: e[0])


def _entries_tar(trial: Path, subset: str, tb: Path) -> list[tuple[str, int, str, bool]]:
    if not tb.is_file():
        return []
    _admit(trial, tb, subset)
    out: list[tuple[str, int, str, bool]] = []
    try:
        with tarfile.open(tb, "r:gz") as tf:
            for m in sorted((m for m in tf.getmembers() if m.isfile()), key=lambda m: m.name):
                suf = Path(m.name).suffix.lower()
                if suf in IMAGE_SUFFIXES:
                    out.append((m.name, m.size, _image_stub(m.name, m.size), False))
                    continue
                if suf not in TEXT_SUFFIXES and suf not in {
                    ".mjs",
                    ".cjs",
                    ".ts",
                    ".tsx",
                    ".jsx",
                    ".css",
                }:
                    out.append((m.name, m.size, _binary_stub(m.name, m.size), False))
                    continue
                fh = tf.extractfile(m)
                if fh is None:
                    continue
                out.append((m.name, m.size, _text(fh.read()), True))
    except (tarfile.TarError, OSError) as exc:
        raise RenderBackendError(f"web workspace tarball unreadable: {tb}: {exc}") from exc
    return out


def _entries_patch(trial: Path, subset: str, patch: Path) -> list[tuple[str, int, str, bool]]:
    if not patch.is_file():
        return []
    body = _text(_read_bytes(trial, patch, subset))
    touched = [
        ln.split(" b/", 1)[1]
        for ln in body.splitlines()
        if ln.startswith("diff --git ") and " b/" in ln
    ]
    head = ("## Files touched\n" + "\n".join(f"- {t}" for t in touched) + "\n\n") if touched else ""
    return [("agent.patch", len(body.encode("utf-8")), head + "## Diff\n" + body, True)]


def _bridge_main() -> None:
    req = json.loads(sys.stdin.read())
    if req.get("func") != "binaries":
        raise SystemExit(f"unknown bridge func {req.get('func')!r}")
    trial, subset = Path(req["trial"]), req["subset"]
    texts: dict[str, str] = {}
    for it in req["items"]:
        p = Path(it["path"])

        _admit(trial, p, subset)
        _, fn = _BINARY_HANDLERS[p.suffix.lower()]
        texts[it["key"]] = fn(p, it["name"])
    print(json.dumps({"texts": texts}))


if __name__ == "__main__":
    if "--bridge" in sys.argv:
        _bridge_main()
    else:
        if len(sys.argv) < 3:
            raise SystemExit("usage: wb_render.py <trial_dir> <subset> [--report|--listing]")
        _t, _s = Path(sys.argv[1]), sys.argv[2]
        if "--listing" in sys.argv:
            print(json.dumps(listing(_t, _s), indent=2, ensure_ascii=False))
        elif "--report" in sys.argv:
            _files, _held = render_report(_t, _s)
            print(
                json.dumps(
                    {"files": {k: len(v) for k, v in _files.items()}, "withheld": _held}, indent=2
                )
            )
        else:
            print(flat_text(_t, _s))
