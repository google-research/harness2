"""Indicadores técnicos puros. Cada función devuelve el valor en el ÚLTIMO elemento de la
secuencia recibida (o ``None`` si no hay suficientes datos), de modo que el llamador decide
exactamente qué historial ve el indicador: nunca puede "mirar" barras futuras.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from trade_agent.domain import Bar

QUANT = Decimal("1e-10")


def _q(value: Decimal) -> Decimal:
    return value.quantize(QUANT)


def sma(values: Sequence[Decimal], period: int) -> Decimal | None:
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return _q(sum(window, Decimal(0)) / period)


def rsi(closes: Sequence[Decimal], period: int) -> Decimal | None:
    """RSI de Wilder. Requiere ``period + 1`` cierres."""
    if period <= 0 or len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, Decimal(0)) for c in changes]
    losses = [max(-c, Decimal(0)) for c in changes]
    avg_gain = sum(gains[:period], Decimal(0)) / period
    avg_loss = sum(losses[:period], Decimal(0)) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return Decimal(100) if avg_gain > 0 else Decimal(50)
    rs = avg_gain / avg_loss
    return _q(Decimal(100) - Decimal(100) / (1 + rs))


def true_range(bar: Bar, previous_close: Decimal) -> Decimal:
    return max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))


def atr(bars: Sequence[Bar], period: int) -> Decimal | None:
    """ATR de Wilder. Requiere ``period + 1`` barras (el primer TR usa el cierre previo)."""
    if period <= 0 or len(bars) < period + 1:
        return None
    ranges = [true_range(bars[i], bars[i - 1].close) for i in range(1, len(bars))]
    value = sum(ranges[:period], Decimal(0)) / period
    for tr in ranges[period:]:
        value = (value * (period - 1) + tr) / period
    return _q(value)
