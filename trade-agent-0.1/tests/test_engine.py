from __future__ import annotations

import copy
import itertools

from conftest import make_config
from test_strategy import DOWN_THEN_UP, series
from trade_agent.data.ingest import Provenance, build_dataset, load_csv
from trade_agent.data.synthetic import write_synthetic_csv
from trade_agent.engine import run_simulation
from trade_agent.persistence import RunStoreError, load_run, write_run

TEST_SPEC = {
    "quote_currency": "MXN", "contract_size": "1", "tick_size": "0.01", "spread": "0.02",
    "commission_per_lot_per_side": "0", "min_lot": "1", "lot_step": "1", "max_lot": "100", "margin_rate": "1",
}


def small_strategy_config(d):
    d["strategy"].update(fast_period=2, slow_period=4, rsi_period=2, atr_period=2, lookback_bars=10, rsi_buy_max="90")
    d["instruments"]["TEST"] = dict(TEST_SPEC)


def types(result):
    return [e["type"] for e in result.events]


def test_signals_are_journaled_but_orders_rejected_without_instrument_spec(dataset):
    config = make_config(lambda d: d.update(instruments={}))
    result = run_simulation([dataset], config)
    assert types(result).count("SIGNAL") == len(dataset.bars)
    rejected = [e for e in result.events if e["type"] == "ORDER_REJECTED"]
    assert rejected and all(e["reason_codes"] == ["MISSING_INSTRUMENT_SPEC"] for e in rejected)
    assert "ORDER_FILLED" not in types(result)


def test_missing_currency_conversion_rejects_orders(dataset):
    config = make_config(lambda d: d["instruments"]["EURUSD"].pop("quote_to_account_rate"))
    rejected = [e for e in run_simulation([dataset], config).events if e["type"] == "ORDER_REJECTED"]
    assert rejected and all("MISSING_COST_INFO" in e["reason_codes"] for e in rejected)
    assert "quote_to_account_rate" in rejected[0]["reasons"][0]


def test_operator_stop_halts_processing_and_is_journaled(dataset, config):
    calls = itertools.count()
    result = run_simulation([dataset], config, should_stop=lambda: next(calls) >= 100)
    stopped = [e for e in result.events if e["type"] == "SYSTEM_STOPPED"]
    assert result.status == "STOPPED" and len(stopped) == 1
    assert stopped[0]["bars_processed"] == 100
    assert types(result).count("SIGNAL") == 100
    assert result.events[-1]["type"] == "RUN_COMPLETED" and result.events[-1]["status"] == "STOPPED"


def test_pending_order_without_next_bar_is_cancelled():
    config = make_config(small_strategy_config)
    dataset = build_dataset("TEST", DOWN_THEN_UP, Provenance.SYNTHETIC)
    result = run_simulation([dataset], config)
    sequence = [t for t in types(result) if t.startswith("ORDER")]
    assert sequence == ["ORDER_PENDING", "ORDER_CANCELLED"]
    cancelled = next(e for e in result.events if e["type"] == "ORDER_CANCELLED")
    assert cancelled["reason"].startswith("NO_NEXT_BAR")


def test_open_position_is_marked_or_closed_at_end_of_data():
    config = make_config(small_strategy_config)
    dataset = build_dataset("TEST", series([10, 9, 8, 7, 6, 5, 9, 9.2]), Provenance.SYNTHETIC)

    kept = run_simulation([dataset], config)
    assert kept.report["positions"] == {**kept.report["positions"], "open": 1, "closed": 0}
    assert kept.report["open_positions"][0]["unrealized_gross_pnl"] is not None

    closed = run_simulation([dataset], config, close_at_end=True)
    [position] = closed.portfolio.closed_positions()
    assert position.exit_reason == "END_OF_DATA" and closed.report["positions"]["open"] == 0


def test_multiple_instruments_respect_max_simultaneous_positions(tmp_path, dataset):
    other = load_csv(write_synthetic_csv(tmp_path / "gbp.csv", instrument="GBPUSD", n_bars=600, seed=11))
    config = make_config(lambda d: (d["instruments"].update(GBPUSD=copy.deepcopy(d["instruments"]["EURUSD"])),
                                    d["risk"].update(max_open_positions=1)))
    result = run_simulation([dataset, other], config)

    open_now, peak = set(), 0
    for e in result.events:
        if e["type"] == "POSITION_OPENED":
            open_now.add(e["position"]["position_id"])
        elif e["type"] == "POSITION_CLOSED":
            open_now.discard(e["position"]["position_id"])
        peak = max(peak, len(open_now))
    filled_instruments = {e["instrument"] for e in result.events if e["type"] == "ORDER_FILLED"}
    assert filled_instruments == {"EURUSD", "GBPUSD"}
    assert peak == 1
    assert result.report["orders"]["rejections_by_reason"].get("MAX_OPEN_POSITIONS", 0) > 0


def test_run_round_trips_through_disk(tmp_path, dataset, config):
    result = run_simulation([dataset], config)
    stored = load_run(write_run(result, tmp_path / "run"))
    assert stored.events == result.events
    assert stored.report == result.report
    assert stored.manifest["journal_sha256"] == result.manifest["journal_sha256"]
    assert stored.manifest["mode"] == "SIMULATION"


def test_loading_a_non_run_directory_fails_clearly(tmp_path):
    try:
        load_run(tmp_path)
    except RunStoreError as exc:
        assert "journal.jsonl" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("se esperaba RunStoreError")
