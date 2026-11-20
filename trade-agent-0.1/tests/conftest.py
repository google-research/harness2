"""Fixtures compartidas. Todos los datos de prueba son SINTÉTICOS y deterministas."""

from __future__ import annotations

import copy

import pytest

from trade_agent.config import AppConfig
from trade_agent.data.ingest import load_csv
from trade_agent.data.synthetic import write_synthetic_csv

BASE_CONFIG = {
    "mode": "simulation",
    "account": {"currency": "MXN", "initial_capital": "100000"},
    "risk": {
        "max_budget_per_trade": "25000",
        "max_loss_per_trade": "600",
        "profit_target_per_trade": "1200",
        "daily_loss_limit": "1500",
        "max_open_positions": 2,
    },
    "strategy": {
        "name": "sma_cross_rsi",
        "fast_period": 10,
        "slow_period": 30,
        "rsi_period": 14,
        "rsi_buy_max": "70",
        "rsi_sell_min": "30",
        "atr_period": 14,
        "stop_atr_multiple": "2",
        "lookback_bars": 200,
    },
    "instruments": {
        "EURUSD": {
            "quote_currency": "USD",
            "quote_to_account_rate": "17.00",
            "contract_size": "100000",
            "tick_size": "0.00001",
            "spread": "0.00012",
            "commission_per_lot_per_side": "60",
            "slippage": "0",
            "min_lot": "0.01",
            "lot_step": "0.01",
            "max_lot": "5",
            "margin_rate": "0.0333",
        }
    },
    "simulation": {"close_open_positions_at_end": False},
}


def make_config(mutate=None) -> AppConfig:
    data = copy.deepcopy(BASE_CONFIG)
    if mutate is not None:
        mutate(data)
    return AppConfig.from_dict(data)


@pytest.fixture
def base_config_dict() -> dict:
    return copy.deepcopy(BASE_CONFIG)


@pytest.fixture
def config() -> AppConfig:
    return make_config()


@pytest.fixture(scope="session")
def synthetic_csv(tmp_path_factory):
    directory = tmp_path_factory.mktemp("data")
    path = directory / "synthetic_eurusd_h1.csv"
    write_synthetic_csv(path, instrument="EURUSD", n_bars=600, seed=7)
    return path


@pytest.fixture
def dataset(synthetic_csv):
    # Instrumento y procedencia se leen del archivo .meta.json generado junto al CSV.
    return load_csv(synthetic_csv)
