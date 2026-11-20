"""Observador de mercado: resumen descriptivo e indicadores del conjunto de datos.

Todo valor se refiere a la ÚLTIMA BARRA DEL ARCHIVO, nunca a "ahora": el resumen siempre
incluye la procedencia (HISTÓRICOS / SINTÉTICOS) y la fecha de esa barra.
"""

from __future__ import annotations

from decimal import Decimal

from trade_agent.config import StrategyParams
from trade_agent.data.ingest import Dataset
from trade_agent.indicators import atr, rsi, sma

SLOPE_BARS = 5


def classify_trend(fast: Decimal | None, slow: Decimal | None, slow_prev: Decimal | None) -> str:
    if None in (fast, slow, slow_prev):
        return "INDETERMINADA (datos insuficientes)"
    slope = slow - slow_prev
    if fast > slow and slope > 0:
        return "ALCISTA"
    if fast < slow and slope < 0:
        return "BAJISTA"
    return "LATERAL / MIXTA"


def observe(dataset: Dataset, params: StrategyParams) -> dict:
    bars = dataset.bars
    closes = [b.close for b in bars]
    fast = sma(closes, params.fast_period)
    slow = sma(closes, params.slow_period)
    slow_prev = sma(closes[:-SLOPE_BARS], params.slow_period) if len(closes) > SLOPE_BARS else None
    first, last = bars[0], bars[-1]
    change_pct = ((last.close - first.open) / first.open * 100).quantize(Decimal("0.01"))
    return {
        **dataset.describe(),
        "as_of": last.timestamp,
        "as_of_note": "Valores al cierre de la última barra del archivo; NO es una cotización actual.",
        "last_close": last.close,
        "period_high": max(b.high for b in bars),
        "period_low": min(b.low for b in bars),
        "period_change_pct": change_pct,
        f"sma_{params.fast_period}": fast,
        f"sma_{params.slow_period}": slow,
        f"rsi_{params.rsi_period}": rsi(closes, params.rsi_period),
        f"atr_{params.atr_period}": atr(bars, params.atr_period),
        "trend": classify_trend(fast, slow, slow_prev),
        "trend_rule": (
            f"ALCISTA si SMA{params.fast_period} > SMA{params.slow_period} y la SMA{params.slow_period} "
            f"subió en las últimas {SLOPE_BARS} barras; BAJISTA en el caso inverso; si no, LATERAL / MIXTA."
        ),
    }
