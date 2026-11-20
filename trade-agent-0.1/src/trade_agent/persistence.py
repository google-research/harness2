"""Persistencia de corridas en disco (JSON / JSON Lines, sin dependencias).

Archivos deterministas de una corrida (mismos datos + configuración -> mismos bytes):

* ``journal.jsonl``   bitácora append-only: un evento por línea, numerado por ``seq``.
* ``report.json``     informe estructurado.  ``report.md``  informe legible.
* ``positions.json``  posiciones abiertas y cerradas.
* ``manifest.json``   run_id, versión, configuración, huellas SHA-256 de datos y bitácora.

``run_meta.json`` (opcional, lo escribe la CLI) guarda la hora de reloj y la duración; es el
único archivo no determinista y queda fuera de la comparación de reproducibilidad.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from trade_agent.presentation import render_report_markdown
from trade_agent.serialization import canonical_json, pretty_json, to_jsonable

DETERMINISTIC_FILES = ("journal.jsonl", "report.json", "report.md", "positions.json", "manifest.json")


class RunStoreError(ValueError):
    pass


@dataclass
class StoredRun:
    path: Path
    events: list[dict]
    report: dict
    positions: list[dict]
    manifest: dict


def write_run(result, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    journal = "".join(canonical_json(event) + "\n" for event in result.events)
    _write(out / "journal.jsonl", journal)
    _write(out / "report.json", pretty_json(result.report))
    _write(out / "report.md", render_report_markdown(result.report))
    positions = [to_jsonable(p.to_dict()) for p in result.portfolio.all_positions()]
    _write(out / "positions.json", pretty_json(positions))
    _write(out / "manifest.json", pretty_json(result.manifest))
    return out


def write_run_meta(out_dir: str | Path, meta: dict) -> None:
    _write(Path(out_dir) / "run_meta.json", pretty_json(meta))


def load_run(run_dir: str | Path) -> StoredRun:
    path = Path(run_dir)
    missing = [name for name in DETERMINISTIC_FILES if not (path / name).exists()]
    if missing:
        raise RunStoreError(f"{path} no es una corrida válida: faltan {', '.join(missing)}.")
    try:
        events = [json.loads(line) for line in (path / "journal.jsonl").read_text("utf-8").splitlines() if line]
        report = json.loads((path / "report.json").read_text("utf-8"))
        positions = json.loads((path / "positions.json").read_text("utf-8"))
        manifest = json.loads((path / "manifest.json").read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise RunStoreError(f"Archivo dañado en {path}: {exc}") from None
    return StoredRun(path, events, report, positions, manifest)


def _write(path: Path, text: str) -> None:
    # newline="\n" evita que Windows convierta saltos de línea y altere las huellas.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
