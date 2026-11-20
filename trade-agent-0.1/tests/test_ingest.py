from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_agent.data.ingest import DataValidationError, Provenance, load_csv
from trade_agent.data.synthetic import generate_bars, write_synthetic_csv

HEADER = "timestamp,open,high,low,close,volume\n"


def write(tmp_path, text, name="d.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_file_is_parsed_with_exact_decimals_and_utc(tmp_path):
    path = write(tmp_path, HEADER + "2020-01-01 00:00:00,1.10000,1.10100,1.09900,1.10050,10\n"
                                     "2020-01-01T02:00:00+01:00,1.10050,1.10100,1.10000,1.10010,0\n")
    ds = load_csv(path, instrument="eurusd", provenance="historical")
    assert ds.instrument == "EURUSD"
    assert ds.provenance is Provenance.HISTORICAL
    assert ds.bars[0].timestamp == datetime(2020, 1, 1, tzinfo=UTC)  # sin zona -> UTC
    assert ds.bars[1].timestamp == datetime(2020, 1, 1, 1, tzinfo=UTC)  # convertido a UTC
    assert ds.bars[0].close == Decimal("1.10050")
    assert len(ds.sha256) == 64


def test_utf8_bom_and_extra_columns_are_accepted(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes(("﻿timestamp,open,high,low,close,volume,spread\n"
                      "2020-01-01T00:00:00Z,1,1.2,0.9,1.1,5,3\n").encode("utf-8"))
    assert len(load_csv(path, instrument="X", provenance="synthetic").bars) == 1


def test_all_problems_are_reported_with_row_numbers(tmp_path):
    path = write(tmp_path, HEADER
                 + "2020-01-01T00:00:00Z,1.1,1.0,1.05,1.1,5\n"     # fila 2: high < low
                 + "2020-01-01T03:00:00Z,1.1,1.2,1.0,abc,5\n"      # fila 3: close no numérico
                 + "2020-01-01T02:00:00Z,1.1,1.2,1.0,1.1,-1\n")    # fila 4: orden y volumen
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path, instrument="EURUSD", provenance="historical")
    rows = sorted((i.row, i.column) for i in excinfo.value.issues)
    assert (2, "high") in rows and (3, "close") in rows
    assert (4, "timestamp") in rows and (4, "volume") in rows
    assert "Fila 4, columna 'timestamp'" in str(excinfo.value)


def test_provenance_comes_from_meta_file_and_cannot_be_contradicted(tmp_path):
    path = write_synthetic_csv(tmp_path / "s.csv", n_bars=50, seed=1)
    meta = json.loads(path.with_suffix(".meta.json").read_text("utf-8"))
    assert meta["provenance"] == "SYNTHETIC"
    ds = load_csv(path)
    assert ds.provenance is Provenance.SYNTHETIC and ds.instrument == "EURUSD"
    assert "SINTÉTICOS" in ds.describe()["notice"]
    with pytest.raises(DataValidationError, match="contradice"):
        load_csv(path, provenance="historical")


def test_unknown_provenance_and_missing_instrument_are_rejected(tmp_path):
    path = write(tmp_path, HEADER + "2020-01-01T00:00:00Z,1,1.2,0.9,1.1,5\n")
    with pytest.raises(DataValidationError, match="procedencia 'live' desconocida"):
        load_csv(path, instrument="X", provenance="live")
    with pytest.raises(DataValidationError, match="instrumento"):
        load_csv(path, provenance="historical")


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(DataValidationError, match="no existe"):
        load_csv(tmp_path / "nope.csv", instrument="X", provenance="historical")


def test_synthetic_generator_is_deterministic_and_valid():
    a = generate_bars(300, seed=42)
    b = generate_bars(300, seed=42)
    c = generate_bars(300, seed=43)
    assert a == b and a != c
    assert all(bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high for bar in a)
    assert all(x.timestamp < y.timestamp for x, y in zip(a, a[1:]))
    assert a[0].timestamp.year == 2020  # nunca se confunde con cotizaciones actuales
