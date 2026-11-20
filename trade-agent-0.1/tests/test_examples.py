"""Los ejemplos versionados son auténticos: se regeneran byte a byte con el código actual."""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_agent.config import load_config
from trade_agent.data.ingest import DataValidationError, Provenance, load_csv
from trade_agent.data.synthetic import write_synthetic_csv
from trade_agent.engine import run_simulation
from trade_agent.persistence import DETERMINISTIC_FILES, write_run

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_example_data_is_synthetic_and_regenerable(tmp_path):
    original = EXAMPLES / "data" / "synthetic_eurusd_h1.csv"
    dataset = load_csv(original)
    assert dataset.provenance is Provenance.SYNTHETIC and len(dataset.bars) == 600
    regenerated = write_synthetic_csv(tmp_path / original.name, n_bars=600, seed=7)
    assert regenerated.read_bytes() == original.read_bytes()


def test_invalid_example_is_rejected_with_every_problem():
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(EXAMPLES / "data" / "invalid_example.csv", instrument="EURUSD", provenance="historical")
    assert {(i.row, i.column) for i in excinfo.value.issues} == {
        (3, "high"), (4, "close"), (5, "volume"), (5, "timestamp")}


def test_committed_sample_run_is_reproduced_exactly(tmp_path):
    dataset = load_csv(EXAMPLES / "data" / "synthetic_eurusd_h1.csv")
    result = run_simulation([dataset], load_config(EXAMPLES / "config.json"))
    fresh = write_run(result, tmp_path / "fresh")
    for name in DETERMINISTIC_FILES:
        assert (fresh / name).read_bytes() == (EXAMPLES / "sample_run" / name).read_bytes(), name
