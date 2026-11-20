from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal as D

from conftest import make_config
from trade_agent.broker import SimulatedBroker
from trade_agent.domain import Bar, OrderRequest, Position, Side

TS = datetime(2020, 1, 10, 12, tzinfo=UTC)
CONFIG = make_config(lambda d: d["instruments"]["EURUSD"].update(slippage="0.00002"))
SPEC = CONFIG.instruments["EURUSD"]


def bar(o, h, l, c):
    return Bar(TS, D(o), D(h), D(l), D(c), D(1))


def pos(side, entry, stop, target):
    return Position("P1", "X", "EURUSD", side, D("0.10"), TS, D(entry), D(stop), D(target),
                    D("500"), D("1"), D("1"), D("6"), D("0"))


def test_fill_prices_include_half_spread_slippage_and_commission():
    broker = SimulatedBroker("MXN")
    base = dict(instrument="EURUSD", signal_ts=TS, reference_price=D("1.1"), stop_distance=D("0.0025"), lots=D("0.10"))
    buy = broker.execute(OrderRequest("B", side=Side.BUY, **base), bar("1.10000", "1.1", "1.1", "1.1"), SPEC)
    sell = broker.execute(OrderRequest("S", side=Side.SELL, **base), bar("1.10000", "1.1", "1.1", "1.1"), SPEC)
    assert buy.price == D("1.10008")   # 1.10000 + 0.00006 + 0.00002
    assert sell.price == D("1.09992")  # 1.10000 - 0.00006 - 0.00002
    assert buy.commission == D("6.0")  # 60 por lote y lado x 0.10


def test_order_without_approved_size_is_rejected():
    broker = SimulatedBroker("MXN")
    report = broker.execute(OrderRequest("Z", "EURUSD", Side.BUY, TS, D("1.1"), D("0.0025")), bar("1.1", "1.1", "1.1", "1.1"), SPEC)
    assert report.status == "REJECTED" and report.reason_code == "INVALID_SIZE"
    assert broker.fill_count == 0


def test_long_protective_exits():
    broker = SimulatedBroker("MXN")
    p = pos(Side.BUY, "1.10008", "1.09758", "1.10508")
    assert broker.check_protective_exit(p, bar("1.10100", "1.10200", "1.10000", "1.10150"), SPEC) is None
    stop = broker.check_protective_exit(p, bar("1.10000", "1.10050", "1.09700", "1.09800"), SPEC)
    assert (stop.reason, stop.price, stop.loss_beyond_plan) == ("STOP_LOSS", D("1.09756"), False)
    target = broker.check_protective_exit(p, bar("1.10400", "1.10600", "1.10300", "1.10500"), SPEC)
    assert (target.reason, target.price) == ("TAKE_PROFIT", D("1.10508"))
    both = broker.check_protective_exit(p, bar("1.10100", "1.10700", "1.09600", "1.10000"), SPEC)
    assert both.reason == "STOP_LOSS"  # criterio conservador
    gap = broker.check_protective_exit(p, bar("1.09500", "1.09600", "1.09400", "1.09500"), SPEC)
    assert (gap.reason, gap.price, gap.loss_beyond_plan) == ("STOP_LOSS", D("1.09492"), True)
    gap_up = broker.check_protective_exit(p, bar("1.11000", "1.11100", "1.10900", "1.11000"), SPEC)
    assert (gap_up.reason, gap_up.price) == ("TAKE_PROFIT", D("1.10994"))


def test_short_protective_exits():
    broker = SimulatedBroker("MXN")
    p = pos(Side.SELL, "1.09992", "1.10242", "1.09492")
    stop = broker.check_protective_exit(p, bar("1.10000", "1.10300", "1.09900", "1.10200"), SPEC)
    assert (stop.reason, stop.price) == ("STOP_LOSS", D("1.10244"))  # stop + deslizamiento
    target = broker.check_protective_exit(p, bar("1.09600", "1.09700", "1.09400", "1.09500"), SPEC)
    assert (target.reason, target.price) == ("TAKE_PROFIT", D("1.09492"))


def test_settlement_is_net_of_round_trip_commission_in_account_currency():
    broker = SimulatedBroker("MXN")
    p = pos(Side.BUY, "1.10008", "1.09758", "1.10508")
    result = broker.settle(p, D("1.10508"), SPEC)
    assert result.gross_pnl == D("850")       # 0.005 x 100000 x 0.10 x 17
    assert result.commission == D("12.0")     # 6 de entrada + 6 de salida
    assert result.net_pnl == D("838.0")
