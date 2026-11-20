from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from trade_agent.domain import Bar
from trade_agent.indicators import atr, rsi, sma


def bars_from(rows):
    start = datetime(2020, 1, 1, tzinfo=UTC)
    return [Bar(start + timedelta(hours=i), D(o), D(h), D(l), D(c), D(1)) for i, (o, h, l, c) in enumerate(rows)]


def test_sma_uses_only_the_last_period_values():
    values = [D(v) for v in (1, 2, 3, 4, 5)]
    assert sma(values, 3) == D(4)
    assert sma(values, 6) is None


def test_rsi_wilder_known_values():
    assert rsi([D(v) for v in (10, 11, 10, 11)], 2) == D(75)
    assert rsi([D(v) for v in (1, 2, 3, 4)], 2) == D(100)
    assert rsi([D(v) for v in (4, 3, 2, 1)], 2) == D(0)
    assert rsi([D(1)] * 4, 2) == D(50)
    assert rsi([D(1), D(2)], 2) is None


def test_atr_constant_range_and_gap():
    flat = bars_from([("10", "11", "9", "10")] * 5)
    assert atr(flat, 3) == D(2)
    gap = bars_from([("10", "11", "9", "10"), ("14", "15", "13", "14")])
    assert atr(gap, 1) == D(5)  # TR = max(2, |15-10|, |13-10|)
    assert atr(gap, 2) is None
