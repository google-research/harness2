from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from conftest import make_config
from trade_agent.domain import OrderRequest, Position, Side
from trade_agent.portfolio import Portfolio
from trade_agent.risk import RiskEngine

TS = datetime(2020, 1, 10, 12, tzinfo=UTC)


def portfolio_for(config):
    return Portfolio(config.account.initial_capital, config.account.currency, config.instruments)


def order(**overrides):
    fields = dict(client_order_id="O1", instrument="EURUSD", side=Side.BUY, signal_ts=TS,
                  reference_price=D("1.10000"), stop_distance=D("0.00250"))
    fields.update(overrides)
    return OrderRequest(**fields)


def evaluate(config, order_, portfolio=None, price=D("1.10000")):
    engine = RiskEngine(config.risk, config.account)
    return engine.evaluate(order_, config.instruments.get(order_.instrument),
                           portfolio or portfolio_for(config), price, TS.date())


def position(instrument="EURUSD", side=Side.BUY, entry="1.10100", planned="500", order_id="X1"):
    entry = D(entry)
    return Position("P0001", order_id, instrument, side, D("0.10"), TS, entry, entry - D("0.0025"),
                    entry + D("0.005"), D(planned), D("1000"), D("187000"), D("6"), D("20.4"))


def with_gbpusd(d):
    d["instruments"]["GBPUSD"] = copy.deepcopy(d["instruments"]["EURUSD"])


def test_sizing_and_potential_loss_in_account_currency():
    decision = evaluate(make_config(), order())
    assert decision.approved, decision.reasons
    b = decision.breakdown
    # pérdida por lote = 0.0025 x 100000 x 17 MXN/USD + 2 x 60 MXN = 4370 MXN -> 600/4370 = 0.137 -> 0.13
    assert decision.lots == D("0.13")
    assert b["binding_limit"] == "max_loss_per_trade"
    assert b["price_risk"] == D("552.5")
    assert b["commission_round_trip"] == D("15.6")
    assert b["max_loss"] == D("568.1")
    assert b["margin"] == D("8095.23")  # 1.1 x 100000 x 17 x 0.0333 x 0.13
    # objetivo neto 1200 MXN: (1200 + 15.6) / (100000 x 0.13 x 17) = 0.0055005 -> 0.00551
    assert decision.take_profit_distance == D("0.00551")
    assert b["expected_net_profit_at_tp"] >= D("1200")


def test_same_trade_in_usd_account_has_different_size():
    config = make_config(lambda d: d["account"].update(currency="USD"))
    decision = evaluate(config, order())
    assert decision.lots == D("1.62")  # 600 / (250 + 120) USD por lote
    assert decision.breakdown["max_loss"] == D("599.4")


@pytest.mark.parametrize(
    ("mutate", "order_kw", "code", "fragment"),
    [
        (lambda d: d["instruments"]["EURUSD"].pop("spread"), {}, "MISSING_COST_INFO", "spread"),
        (lambda d: d["instruments"]["EURUSD"].pop("contract_size"), {}, "MISSING_COST_INFO", "contract_size"),
        (lambda d: d["instruments"]["EURUSD"].pop("quote_to_account_rate"), {}, "MISSING_COST_INFO",
         "quote_to_account_rate"),
        (None, {"instrument": "GBPUSD"}, "MISSING_INSTRUMENT_SPEC", "GBPUSD"),
        (None, {"stop_distance": None}, "MISSING_STOP", "stop"),
        (None, {"stop_distance": D("0.00010")}, "STOP_INSIDE_SPREAD", "spread"),
        (lambda d: d["risk"].update(max_budget_per_trade="100"), {}, "SIZE_BELOW_MIN_LOT", "max_budget_per_trade"),
        (lambda d: d["risk"].update(max_loss_per_trade="10"), {}, "SIZE_BELOW_MIN_LOT", "max_loss_per_trade"),
    ],
)
def test_orders_without_sufficient_information_or_room_are_rejected(mutate, order_kw, code, fragment):
    decision = evaluate(make_config(mutate), order(**order_kw))
    assert not decision.approved
    assert code in decision.reason_codes
    assert fragment in " ".join(decision.reasons)
    assert decision.lots is None


def test_duplicate_order_is_rejected():
    config = make_config()
    book = portfolio_for(config)
    book.executed_order_ids.add("O1")
    assert "DUPLICATE_ORDER" in evaluate(config, order(), book).reason_codes


def test_adding_to_a_losing_position_is_prohibited():
    config = make_config()
    book = portfolio_for(config)
    book.add(position(entry="1.10100"))
    book.mark("EURUSD", D("1.09900"))
    decision = evaluate(config, order(client_order_id="O2"), book, price=D("1.09900"))
    assert decision.reason_codes == ["ADD_TO_LOSING_POSITION"]


def test_second_position_in_same_instrument_is_rejected_even_if_winning():
    config = make_config()
    book = portfolio_for(config)
    book.add(position(entry="1.10100"))
    book.mark("EURUSD", D("1.10500"))
    assert evaluate(config, order(client_order_id="O2"), book).reason_codes == ["POSITION_ALREADY_OPEN"]


def test_max_simultaneous_positions():
    config = make_config(lambda d: (with_gbpusd(d), d["risk"].update(max_open_positions=1)))
    book = portfolio_for(config)
    book.add(position(instrument="GBPUSD"))
    assert evaluate(config, order(), book).reason_codes == ["MAX_OPEN_POSITIONS"]


def test_daily_loss_limit_reached_blocks_new_orders():
    config = make_config()
    book = portfolio_for(config)
    p = position()
    book.add(p)
    book.close(p, TS, D("1.0"), "STOP_LOSS", gross=D("-1494"), commission_total=D("6"))
    assert book.realized_pnl_on(TS.date()) == D("-1500")
    assert evaluate(config, order(client_order_id="O2"), book).reason_codes == ["DAILY_LOSS_LIMIT_REACHED"]


def test_remaining_daily_budget_and_open_risk_shrink_the_position():
    config = make_config(with_gbpusd)
    book = portfolio_for(config)
    book.add(position(instrument="GBPUSD", planned="1000"))  # riesgo abierto 1000 de 1500
    decision = evaluate(config, order(), book)
    assert decision.approved, decision.reasons
    assert decision.breakdown["binding_limit"] == "daily_loss_limit"
    assert decision.lots == D("0.11")  # 500 / 4370 = 0.114
    assert D(1000) + decision.breakdown["max_loss"] <= config.risk.daily_loss_limit


def test_every_approved_decision_respects_all_limits():
    config = make_config()
    decision = evaluate(config, order())
    passed = {c.code for c in decision.checks if c.passed}
    assert {"MAX_LOSS_PER_TRADE", "MAX_BUDGET_PER_TRADE", "DAILY_LOSS_LIMIT", "FREE_MARGIN",
            "MAX_OPEN_POSITIONS", "COST_INFO_COMPLETE"} <= passed
