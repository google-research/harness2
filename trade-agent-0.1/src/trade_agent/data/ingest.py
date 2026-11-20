"""Carga y validación de datos OHLCV desde CSV.

Formato aceptado (cabecera obligatoria, columnas adicionales se ignoran)::

    timestamp,open,high,low,close,volume
    2020-01-06T00:00:00Z,1.10000,1.10050,1.09950,1.10020,850

* ``timestamp``: ISO 8601. Sin zona horaria se interpreta como UTC. Es la hora de
  apertura de la barra. Debe ser estrictamente creciente (sin duplicados).
* Precios > 0; ``low <= open, close <= high``; ``volume >= 0``.

La procedencia (HISTÓRICOS o SINTÉTICOS) es obligatoria: se declara con el argumento
``provenance`` o con un archivo ``<nombre>.meta.json`` junto al CSV. Un conjunto marcado
como sintético nunca puede cargarse como histórico.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from trade_agent.domain import Bar

REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
PRICE_COLUMNS = ("open", "high", "low", "close")
MAX_REPORTED_ISSUES = 20


class Provenance(str, Enum):
    HISTORICAL = "HISTORICAL"
    SYNTHETIC = "SYNTHETIC"

    @property
    def label(self) -> str:
        return "HISTÓRICOS" if self is Provenance.HISTORICAL else "SINTÉTICOS"

    @property
    def notice(self) -> str:
        if self is Provenance.SYNTHETIC:
            return (
                "Datos SINTÉTICOS generados por computadora: no son cotizaciones de mercado "
                "reales ni actuales."
            )
        return (
            "Datos HISTÓRICOS declarados por el usuario: describen el pasado y no son "
            "cotizaciones actuales."
        )

    @classmethod
    def parse(cls, value: "Provenance | str") -> "Provenance":
        if isinstance(value, Provenance):
            return value
        normalized = str(value).strip().upper()
        aliases = {"HISTORICO": "HISTORICAL", "HISTÓRICO": "HISTORICAL", "SINTETICO": "SYNTHETIC",
                   "SINTÉTICO": "SYNTHETIC"}
        normalized = aliases.get(normalized, normalized)
        try:
            return cls(normalized)
        except ValueError:
            raise DataValidationError(
                [DataIssue(None, None, f"procedencia '{value}' desconocida; use 'historical' o 'synthetic'.")]
            ) from None


@dataclass(frozen=True)
class DataIssue:
    row: int | None
    column: str | None
    message: str

    def __str__(self) -> str:
        location = []
        if self.row is not None:
            location.append(f"Fila {self.row}")
        if self.column is not None:
            location.append(f"columna '{self.column}'")
        prefix = ", ".join(location)
        return f"{prefix}: {self.message}" if prefix else self.message


class DataValidationError(ValueError):
    """Datos rechazados. ``issues`` contiene cada problema con su fila y columna."""

    def __init__(self, issues: Sequence[DataIssue], source: str | None = None):
        self.issues = list(issues)
        self.source = source
        super().__init__(self._render())

    def _render(self) -> str:
        where = f" ({self.source})" if self.source else ""
        lines = [f"Datos rechazados{where}: {len(self.issues)} problema(s)."]
        for issue in self.issues[:MAX_REPORTED_ISSUES]:
            lines.append(f"  - {issue}")
        if len(self.issues) > MAX_REPORTED_ISSUES:
            lines.append(f"  ... y {len(self.issues) - MAX_REPORTED_ISSUES} problema(s) más.")
        return "\n".join(lines)


@dataclass(frozen=True)
class Dataset:
    instrument: str
    bars: tuple[Bar, ...]
    provenance: Provenance
    source: str
    sha256: str
    timeframe: str | None = None

    @property
    def first_ts(self) -> datetime:
        return self.bars[0].timestamp

    @property
    def last_ts(self) -> datetime:
        return self.bars[-1].timestamp

    def describe(self) -> dict:
        return {
            "instrument": self.instrument,
            "provenance": self.provenance.value,
            "provenance_label": self.provenance.label,
            "notice": self.provenance.notice,
            "source": self.source,
            "rows": len(self.bars),
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "timeframe": self.timeframe,
            "sha256": self.sha256,
        }


def parse_timestamp(text: str) -> datetime:
    value = datetime.fromisoformat(text.strip())
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_decimal(text: str | None) -> Decimal:
    if text is None or not text.strip():
        raise ValueError("valor vacío")
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise ValueError(f"'{text.strip()}' no es un número válido") from None
    if not value.is_finite():
        raise ValueError(f"'{text.strip()}' no es un número finito")
    return value


def validate_bars(bars: Sequence[Bar], rows: Sequence[int] | None = None) -> list[DataIssue]:
    """Coherencia OHLC y orden cronológico estricto. ``rows`` numera cada barra (línea del CSV)."""
    if not bars:
        return [DataIssue(None, None, "El archivo no contiene filas de datos.")]
    rows = list(rows) if rows is not None else list(range(1, len(bars) + 1))
    issues: list[DataIssue] = []
    for bar, row in zip(bars, rows):
        issues.extend(_ohlc_issues(bar, row))
    issues.extend(_chronology_issues([(row, bar.timestamp) for bar, row in zip(bars, rows)]))
    return issues


def _chronology_issues(stamps: Sequence[tuple[int, datetime]]) -> list[DataIssue]:
    """Orden estrictamente creciente, evaluado sobre toda fila cuyo timestamp sea legible."""
    issues = []
    for (_, previous), (row, current) in zip(stamps, stamps[1:]):
        if current == previous:
            issues.append(DataIssue(row, "timestamp", f"timestamp duplicado {current.isoformat()}."))
        elif current < previous:
            issues.append(
                DataIssue(
                    row,
                    "timestamp",
                    f"{current.isoformat()} no respeta el orden cronológico ascendente "
                    f"(la fila anterior es {previous.isoformat()}).",
                )
            )
    return issues


def _ohlc_issues(bar: Bar, row: int) -> list[DataIssue]:
    issues = []
    for column in PRICE_COLUMNS:
        if getattr(bar, column) <= 0:
            issues.append(DataIssue(row, column, f"el precio {getattr(bar, column)} debe ser mayor que cero."))
    if bar.volume < 0:
        issues.append(DataIssue(row, "volume", f"el volumen {bar.volume} no puede ser negativo."))
    if bar.high < max(bar.open, bar.close, bar.low):
        issues.append(
            DataIssue(row, "high", f"high ({bar.high}) es menor que open, close o low; la barra es incoherente.")
        )
    if bar.low > min(bar.open, bar.close, bar.high):
        issues.append(
            DataIssue(row, "low", f"low ({bar.low}) es mayor que open, close o high; la barra es incoherente.")
        )
    return issues


def _canonical_bytes(bars: Iterable[Bar]) -> bytes:
    lines = [",".join(REQUIRED_COLUMNS)]
    for b in bars:
        lines.append(f"{b.timestamp.isoformat()},{b.open},{b.high},{b.low},{b.close},{b.volume}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_dataset(
    instrument: str,
    bars: Sequence[Bar],
    provenance: Provenance | str,
    source: str = "memoria",
    timeframe: str | None = None,
) -> Dataset:
    """Construye un ``Dataset`` desde barras en memoria aplicando las mismas validaciones."""
    issues = validate_bars(bars)
    if issues:
        raise DataValidationError(issues, source)
    return Dataset(
        instrument=_validate_instrument(instrument, source),
        bars=tuple(bars),
        provenance=Provenance.parse(provenance),
        source=source,
        sha256=hashlib.sha256(_canonical_bytes(bars)).hexdigest(),
        timeframe=timeframe,
    )


def meta_path_for(csv_path: Path) -> Path:
    return csv_path.with_suffix(".meta.json")


def _read_meta(csv_path: Path) -> dict:
    path = meta_path_for(csv_path)
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataValidationError([DataIssue(None, None, f"{path.name} no es JSON válido: {exc}")], str(path))
    if not isinstance(meta, dict):
        raise DataValidationError([DataIssue(None, None, f"{path.name} debe contener un objeto JSON.")], str(path))
    return meta


def _validate_instrument(instrument: str | None, source: str) -> str:
    if not instrument or not str(instrument).strip():
        raise DataValidationError(
            [DataIssue(None, None, "no se indicó el instrumento (use --instrument o el archivo .meta.json).")],
            source,
        )
    return str(instrument).strip().upper()


def _resolve_provenance(explicit: Provenance | str | None, meta: dict, source: str) -> Provenance:
    declared = Provenance.parse(meta["provenance"]) if meta.get("provenance") else None
    chosen = Provenance.parse(explicit) if explicit is not None else None
    if chosen and declared and chosen != declared:
        raise DataValidationError(
            [
                DataIssue(
                    None,
                    None,
                    f"la procedencia indicada ({chosen.value}) contradice la del archivo .meta.json "
                    f"({declared.value}); no se permite presentar datos sintéticos como históricos.",
                )
            ],
            source,
        )
    resolved = chosen or declared
    if resolved is None:
        raise DataValidationError(
            [
                DataIssue(
                    None,
                    None,
                    "procedencia no declarada: indique si los datos son históricos o sintéticos "
                    "(--provenance historical|synthetic o archivo .meta.json).",
                )
            ],
            source,
        )
    return resolved


def load_csv(
    path: str | Path,
    instrument: str | None = None,
    provenance: Provenance | str | None = None,
    timeframe: str | None = None,
) -> Dataset:
    path = Path(path)
    source = path.name
    if not path.exists():
        raise DataValidationError([DataIssue(None, None, f"no existe el archivo {path}.")], source)
    meta = _read_meta(path)
    instrument = _validate_instrument(instrument or meta.get("instrument"), source)
    resolved_provenance = _resolve_provenance(provenance, meta, source)
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DataValidationError([DataIssue(None, None, "el archivo no está codificado en UTF-8.")], source)

    reader = csv.DictReader(io.StringIO(text))
    header = [name.strip().lower() for name in (reader.fieldnames or [])]
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        issues = [
            DataIssue(1, None, f"Falta la columna '{column}'. Columnas requeridas: {', '.join(REQUIRED_COLUMNS)}.")
            for column in missing
        ]
        raise DataValidationError(issues, source)
    reader.fieldnames = header

    bars: list[Bar] = []
    issues: list[DataIssue] = []
    stamps: list[tuple[int, datetime]] = []  # toda fila con timestamp legible, para el orden
    for record in reader:
        row = reader.line_num
        row_issues: list[DataIssue] = []
        values: dict[str, object] = {}
        try:
            values["timestamp"] = parse_timestamp(record.get("timestamp") or "")
            stamps.append((row, values["timestamp"]))
        except ValueError:
            row_issues.append(
                DataIssue(row, "timestamp", f"'{record.get('timestamp')}' no es una fecha ISO 8601 válida.")
            )
        for column in REQUIRED_COLUMNS[1:]:
            try:
                values[column] = _parse_decimal(record.get(column))
            except ValueError as exc:
                row_issues.append(DataIssue(row, column, str(exc) + "."))
        if row_issues:
            issues.extend(row_issues)
            continue
        bar = Bar(**values)  # type: ignore[arg-type]
        bars.append(bar)
        issues.extend(_ohlc_issues(bar, row))

    issues.extend(_chronology_issues(stamps))
    if not bars and not issues:
        issues.append(DataIssue(None, None, "El archivo no contiene filas de datos."))
    if issues:
        raise DataValidationError(sorted(issues, key=lambda i: i.row or 0), source)

    return Dataset(
        instrument=instrument,
        bars=tuple(bars),
        provenance=resolved_provenance,
        source=source,
        sha256=hashlib.sha256(raw).hexdigest(),
        timeframe=timeframe or meta.get("timeframe"),
    )
