from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from trade_agent.config import StrategyParams
from trade_agent.data.ingest import Provenance
from trade_agent.domain import Action, Bar
from trade_agent.strategy import SmaCrossRsiStrategy, build_strategy, generate_signals

SMALL = dict(fast_period=2, slow_period=4, rsi_period=2, atr_period=2, lookback_bars=10)


def series(closes):
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars, prev = [], D(closes[0])
    for i, c in enumerate(closes):
        c = D(c)
        bars.append(Bar(start + timedelta(hours=i), prev, max(prev, c) + D("0.5"), min(prev, c) - D("0.5"), c, D(1)))
        prev = c
    return bars


DOWN_THEN_UP = series([10, 9, 8, 7, 6, 5, 9])  # cruce alcista en la última barra, RSI(2)=80
UP_THEN_DOWN = series([5, 6, 7, 8, 9, 10, 6])  # cruce bajista en la última barra, RSI(2)=20


def evaluate(bars, **overrides):
    strategy = SmaCrossRsiStrategy(StrategyParams(**{**SMALL, **overrides}))
    return strategy.evaluate("TEST", bars, Provenance.SYNTHETIC)


def test_bullish_cross_with_rsi_below_threshold_is_buy():
    signal = evaluate(DOWN_THEN_UP, rsi_buy_max=D(90))
    assert signal.action is Action.BUY
    assert signal.conditions["crossed_up"] is True
    assert signal.conditions["rsi_2"] == D(80)
    assert signal.reference_price == D(9)
    assert signal.timestamp == DOWN_THEN_UP[-1].timestamp
    assert signal.stop_distance == signal.conditions["atr_2"] * 2
    assert "SMA2 cruzó por encima de SMA4" in signal.justification
    assert signal.data["bars_used"] == len(DOWN_THEN_UP)
    assert signal.data["provenance"] == "SYNTHETIC"


def test_bullish_cross_filtered_by_rsi_is_no_trade_with_reason():
    signal = evaluate(DOWN_THEN_UP, rsi_buy_max=D(70))
    assert signal.action is Action.NO_TRADE
    assert "filtro" in signal.justification and signal.stop_distance is None


def test_bearish_cross_is_sell_or_filtered():
    assert evaluate(UP_THEN_DOWN, rsi_sell_min=D(10)).action is Action.SELL
    filtered = evaluate(UP_THEN_DOWN, rsi_sell_min=D(30))
    assert filtered.action is Action.NO_TRADE and "filtro" in filtered.justification


def test_no_cross_and_warmup_are_no_trade():
    assert evaluate(series([1, 2, 3, 4, 5, 6, 7])).action is Action.NO_TRADE
    warm = evaluate(DOWN_THEN_UP[:3])
    assert warm.action is Action.NO_TRADE
    assert warm.conditions["warmup_complete"] is False
    assert "insuficientes" in warm.justification


def test_strategy_only_sees_the_configured_lookback():
    strategy = SmaCrossRsiStrategy(StrategyParams(**{**SMALL, "lookback_bars": 7}))
    long_history = series([20] * 30 + [10, 9, 8, 7, 6, 5, 9])
    assert strategy.evaluate("T", long_history, Provenance.SYNTHETIC) == strategy.evaluate(
        "T", long_history[-7:], Provenance.SYNTHETIC)


def test_justifications_make_no_predictive_claims(dataset):
    banned = ("subirá", "bajará", "garantiza", "seguro que", "predice", "will ", "guarantee")
    signals = generate_signals(dataset, build_strategy(StrategyParams()))
    assert any(s.action is not Action.NO_TRADE for s in signals)
    for s in signals:
        text = s.justification.lower()
        assert not any(word in text for word in banned), text
        assert "no es un pronóstico" in text
