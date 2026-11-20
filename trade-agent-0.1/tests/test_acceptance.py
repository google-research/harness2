"""Contratos de aceptación de Trade Agent 0.1 (uno o más tests por criterio del enunciado)."""

from __future__ import annotations

import ast
import dataclasses
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import make_config
from trade_agent import cli
from trade_agent.broker import (
    LiveTradingDisabledError,
    MetaTrader5Broker,
    SimulatedBroker,
    create_broker,
)
from trade_agent.config import ConfigError
from trade_agent.data.ingest import DataValidationError, Provenance, build_dataset, load_csv
from trade_agent.domain import OrderRequest, Side
from trade_agent.engine import run_simulation
from trade_agent.persistence import write_run
from trade_agent.strategy import build_strategy, generate_signals

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "trade_agent"


# --- Criterio 1: datos idénticos -> resultados reproducibles -------------------------------


def test_identical_inputs_produce_identical_results(dataset, config, tmp_path):
    first = run_simulation([dataset], config)
    second = run_simulation([dataset], config)

    assert first.run_id == second.run_id
    assert first.events == second.events
    assert first.report == second.report

    dir_a = write_run(first, tmp_path / "a")
    dir_b = write_run(second, tmp_path / "b")
    for name in ("journal.jsonl", "report.json", "report.md", "positions.json", "manifest.json"):
        assert (dir_a / name).read_bytes() == (dir_b / name).read_bytes(), name


# --- Criterio 2: ninguna operación supera los límites configurados -------------------------


def test_no_filled_order_exceeds_configured_limits(dataset, config):
    result = run_simulation([dataset], config, close_at_end=True)
    fills = [e for e in result.events if e["type"] == "ORDER_FILLED"]
    assert fills, "el escenario debe producir al menos una operación simulada"

    limits = config.risk
    for fill in fills:
        risk = fill["risk"]
        assert Decimal(risk["max_loss"]) <= limits.max_loss_per_trade
        assert Decimal(risk["margin"]) <= limits.max_budget_per_trade
        assert fill["open_positions_after"] <= limits.max_open_positions

    # La pérdida neta realizada de cada día nunca supera el límite diario.
    daily = defaultdict(Decimal)
    for position in result.portfolio.closed_positions():
        daily[position.exit_ts.date()] += position.net_pnl
    assert daily
    assert all(total >= -limits.daily_loss_limit for total in daily.values())

    # Ninguna pérdida realizada supera la pérdida máxima por operación.
    for position in result.portfolio.closed_positions():
        assert -position.net_pnl <= limits.max_loss_per_trade


def test_orders_violating_limits_are_rejected_not_executed(dataset):
    # Un presupuesto ínfimo hace imposible incluso el lote mínimo.
    config = make_config(lambda d: d["risk"].update(max_budget_per_trade="10"))
    result = run_simulation([dataset], config)

    assert not [e for e in result.events if e["type"] == "ORDER_FILLED"]
    rejected = [e for e in result.events if e["type"] == "ORDER_REJECTED"]
    assert rejected
    assert all("SIZE_BELOW_MIN_LOT" in e["reason_codes"] for e in rejected)


# --- Criterio 3: las señales no utilizan información futura --------------------------------


def _with_altered_future(dataset, cut_index):
    altered = list(dataset.bars[: cut_index + 1])
    for bar in dataset.bars[cut_index + 1 :]:
        factor = Decimal("1.03")
        altered.append(
            dataclasses.replace(
                bar,
                open=bar.open * factor,
                high=bar.high * factor,
                low=bar.low * factor,
                close=bar.close * factor,
            )
        )
    return build_dataset(dataset.instrument, altered, dataset.provenance, source="test-altered")


def test_signals_do_not_depend_on_future_bars(dataset, config):
    strategy = build_strategy(config.strategy)
    cut = 350
    full = generate_signals(dataset, strategy)
    truncated = build_dataset(
        dataset.instrument, dataset.bars[: cut + 1], dataset.provenance, source="test-truncated"
    )
    altered = _with_altered_future(dataset, cut)

    assert generate_signals(truncated, strategy) == full[: cut + 1]
    assert generate_signals(altered, strategy)[: cut + 1] == full[: cut + 1]


def test_engine_decisions_do_not_depend_on_future_bars(dataset, config):
    cut = 350
    cut_ts = dataset.bars[cut].timestamp.isoformat()
    original = run_simulation([dataset], config)
    altered = run_simulation([_with_altered_future(dataset, cut)], config)

    def prefix(result):
        return [e for e in result.events if e.get("bar_ts") is not None and e["bar_ts"] <= cut_ts]

    assert prefix(original)
    assert prefix(original) == prefix(altered)


# --- Criterio 4: una orden duplicada no produce dos ejecuciones ----------------------------


def test_duplicate_order_is_executed_only_once(dataset, config):
    broker = SimulatedBroker(config.account.currency)
    spec = config.instruments["EURUSD"]
    bar = dataset.bars[100]
    order = OrderRequest(
        client_order_id="EURUSD-TEST-BUY",
        instrument="EURUSD",
        side=Side.BUY,
        signal_ts=dataset.bars[99].timestamp,
        reference_price=dataset.bars[99].close,
        stop_distance=Decimal("0.00250"),
        lots=Decimal("0.10"),
    )

    first = broker.execute(order, bar, spec)
    second = broker.execute(order, bar, spec)

    assert first.status == "FILLED"
    assert second.status == "REJECTED"
    assert second.reason_code == "DUPLICATE_ORDER"
    assert broker.fill_count == 1


def test_engine_never_fills_the_same_client_order_id_twice(dataset, config):
    result = run_simulation([dataset], config)
    ids = [e["client_order_id"] for e in result.events if e["type"] == "ORDER_FILLED"]
    assert ids
    assert len(ids) == len(set(ids))


# --- Criterio 5: distinción entre posiciones abiertas y cerradas ---------------------------


def test_open_and_closed_positions_are_distinguished(dataset, config):
    full = run_simulation([dataset], config)
    fill_bars = [e["bar_index"] for e in full.events if e["type"] == "ORDER_FILLED"]
    assert len(fill_bars) >= 2
    # Detiene el sistema justo después de la segunda apertura: quedan posiciones de ambos tipos.
    stopped = run_simulation([dataset], config, stop_after_bars=fill_bars[1] + 1)

    open_positions = stopped.portfolio.open_positions()
    closed_positions = stopped.portfolio.closed_positions()
    assert open_positions and closed_positions
    assert {p.position_id for p in open_positions}.isdisjoint(
        {p.position_id for p in closed_positions}
    )
    for p in open_positions:
        assert p.status == "OPEN" and p.exit_price is None and p.net_pnl is None
    for p in closed_positions:
        assert p.status == "CLOSED" and p.exit_price is not None and p.net_pnl is not None

    types = [e["type"] for e in stopped.events]
    assert types.count("POSITION_OPENED") == len(open_positions) + len(closed_positions)
    assert types.count("POSITION_CLOSED") == len(closed_positions)
    assert "SYSTEM_STOPPED" in types
    assert stopped.report["positions"]["open"] == len(open_positions)
    assert stopped.report["positions"]["closed"] == len(closed_positions)


# --- Criterio 6: los costes afectan al resultado económico ---------------------------------


def test_costs_change_the_economic_result_of_the_same_trade(dataset, config, base_config_dict):
    free = make_config(
        lambda d: d["instruments"]["EURUSD"].update(spread="0", commission_per_lot_per_side="0")
    )
    order = OrderRequest(
        client_order_id="COST-TEST",
        instrument="EURUSD",
        side=Side.BUY,
        signal_ts=dataset.bars[199].timestamp,
        reference_price=dataset.bars[199].close,
        stop_distance=Decimal("0.00250"),
        lots=Decimal("0.50"),
    )
    entry_bar, exit_bar = dataset.bars[200], dataset.bars[210]

    def round_trip(cfg):
        broker = SimulatedBroker(cfg.account.currency)
        spec = cfg.instruments["EURUSD"]
        fill = broker.execute(order, entry_bar, spec)
        return broker.close_at_market(fill, exit_bar.close, spec)

    with_costs = round_trip(config)
    without_costs = round_trip(free)
    spec = config.instruments["EURUSD"]
    expected_spread_cost = spec.spread * spec.contract_size * order.lots * spec.quote_to_account_rate
    expected_commission = 2 * spec.commission_per_lot_per_side * order.lots

    assert with_costs.net_pnl < without_costs.net_pnl
    assert without_costs.net_pnl - with_costs.net_pnl == expected_spread_cost + expected_commission


def test_reported_results_are_net_of_costs(dataset, config):
    result = run_simulation([dataset], config, close_at_end=True)
    closed = result.portfolio.closed_positions()
    assert closed
    for p in closed:
        assert p.commission > 0
        assert p.net_pnl == p.gross_pnl - p.commission
    totals = result.report["totals"]
    assert Decimal(totals["net_pnl"]) == Decimal(totals["gross_pnl"]) - Decimal(totals["commissions"])
    assert Decimal(totals["spread_cost"]) > 0


# --- Criterio 7: los datos inválidos provocan rechazos comprensibles -----------------------

HEADER = "timestamp,open,high,low,close,volume\n"
ROW_A = "2020-01-01T00:00:00Z,1.1000,1.1010,1.0990,1.1005,100\n"
ROW_B = "2020-01-01T01:00:00Z,1.1005,1.1015,1.0995,1.1010,100\n"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (HEADER + ROW_B + ROW_A, "orden cronológico"),
        (HEADER + ROW_A + ROW_A, "duplicado"),
        ("timestamp,open,high,low,close\n" + ROW_A, "Falta la columna 'volume'"),
        (HEADER + "2020-01-01T00:00:00Z,abc,1.1010,1.0990,1.1005,100\n", "no es un número"),
        (HEADER + "2020-01-01T00:00:00Z,1.1000,1.0980,1.0990,1.1005,100\n", "high"),
        (HEADER + "2020-01-01T00:00:00Z,-1.1,1.1010,1.0990,1.1005,100\n", "mayor que cero"),
        (HEADER + "ayer,1.1000,1.1010,1.0990,1.1005,100\n", "ISO 8601"),
        (HEADER, "no contiene filas"),
    ],
)
def test_invalid_data_is_rejected_with_clear_message(tmp_path, content, expected):
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path, instrument="EURUSD", provenance=Provenance.HISTORICAL)
    assert expected in str(excinfo.value)


def test_data_without_declared_provenance_is_rejected(tmp_path):
    path = tmp_path / "unknown.csv"
    path.write_text(HEADER + ROW_A + ROW_B, encoding="utf-8")
    with pytest.raises(DataValidationError) as excinfo:
        load_csv(path, instrument="EURUSD")
    assert "procedencia" in str(excinfo.value)


def test_cli_reports_invalid_data_without_traceback(tmp_path, capsys):
    path = tmp_path / "bad.csv"
    path.write_text(HEADER + ROW_B + ROW_A, encoding="utf-8")
    code = cli.main(["load", str(path), "--instrument", "EURUSD", "--provenance", "historical"])
    captured = capsys.readouterr()
    assert code == 2
    assert "orden cronológico" in captured.err
    assert "Traceback" not in captured.err + captured.out


# --- Criterio 8: el modo real no existe o queda técnicamente deshabilitado -----------------


def test_live_mode_cannot_be_configured(base_config_dict):
    base_config_dict["mode"] = "live"
    with pytest.raises(ConfigError, match="deshabilitado"):
        make_config(lambda d: d.update(mode="live"))


@pytest.mark.parametrize("key", ["password", "login", "server", "token", "api_key"])
def test_broker_credentials_are_refused(key):
    with pytest.raises(ConfigError, match="credenciales"):
        make_config(lambda d: d["account"].update({key: "x"}))


def test_live_broker_is_technically_disabled():
    with pytest.raises(LiveTradingDisabledError):
        create_broker("live", "MXN")
    with pytest.raises(LiveTradingDisabledError):
        MetaTrader5Broker()
    assert create_broker("simulation", "MXN").mode == "SIMULATION"


def test_package_imports_no_network_or_broker_libraries():
    forbidden = {"socket", "ssl", "requests", "urllib", "http", "MetaTrader5", "websocket", "aiohttp"}
    imported = set()
    for source in PACKAGE_DIR.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(forbidden), imported & forbidden


# --- Criterio 10: demostración de un ciclo completo -----------------------------------------


def test_demo_runs_the_full_cycle(tmp_path, capsys):
    code = cli.main(["demo", "--out", str(tmp_path)])
    output = capsys.readouterr().out
    assert code == 0

    run_dirs = [p for p in (tmp_path / "runs").iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    events = [json.loads(line) for line in (run_dirs[0] / "journal.jsonl").read_text("utf-8").splitlines()]
    types = {e["type"] for e in events}
    assert {"DATA_LOADED", "SIGNAL", "RISK_CHECK", "ORDER_PENDING", "ORDER_FILLED",
            "POSITION_OPENED", "POSITION_CLOSED", "RUN_COMPLETED"} <= types
    assert any(e["type"] == "SIGNAL" and e["action"] in ("BUY", "SELL") for e in events)
    report = (run_dirs[0] / "report.md").read_text("utf-8")
    assert "SINTÉTICOS" in report
    assert "SINTÉTICOS" in output
