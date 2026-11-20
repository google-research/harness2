"""Motor de señales: estrategias deterministas basadas en reglas explícitas.

Contrato anti-anticipación: ``Strategy.evaluate`` recibe solo el historial hasta la barra
evaluada (inclusive) y emite la señal "al cierre" de esa barra. La ejecución, si la hay,
ocurre en la apertura de la barra SIGUIENTE (ver ``engine``). Las justificaciones describen
condiciones observadas; no contienen pronósticos.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from trade_agent.config import ConfigError, StrategyParams
from trade_agent.data.ingest import Dataset, Provenance
from trade_agent.domain import Action, Bar, Signal
from trade_agent.indicators import atr, rsi, sma

DISCLAIMER = "Condición técnica observada en datos pasados; no es un pronóstico."


class Strategy(ABC):
    name: str

    @property
    @abstractmethod
    def lookback(self) -> int:
        """Número máximo de barras recientes que la estrategia necesita ver."""

    @abstractmethod
    def evaluate(self, instrument: str, history: Sequence[Bar], provenance: Provenance) -> Signal:
        """Evalúa al cierre de ``history[-1]`` usando únicamente ``history``."""


class SmaCrossRsiStrategy(Strategy):
    """BUY: la SMA rápida cruza por encima de la lenta y RSI < ``rsi_buy_max``.
    SELL: la SMA rápida cruza por debajo de la lenta y RSI > ``rsi_sell_min``.
    En otro caso NO_TRADE. Distancia de stop propuesta: ``stop_atr_multiple`` x ATR.
    """

    name = "sma_cross_rsi"

    def __init__(self, params: StrategyParams):
        self.p = params

    @property
    def lookback(self) -> int:
        return self.p.lookback_bars

    def evaluate(self, instrument: str, history: Sequence[Bar], provenance: Provenance) -> Signal:
        p = self.p
        bars = list(history[-p.lookback_bars :])
        last = bars[-1]
        data = {
            "bars_used": len(bars),
            "window_start": bars[0].timestamp,
            "window_end": last.timestamp,
            "evaluated_at": "cierre de la barra window_end",
            "fields": ["high", "low", "close"],
            "provenance": provenance.value,
        }

        def signal(action: Action, conditions: dict, text: str, stop=None) -> Signal:
            return Signal(
                instrument=instrument,
                timestamp=last.timestamp,
                reference_price=last.close,
                action=action,
                conditions=conditions,
                justification=f"{text} {DISCLAIMER}",
                data=data,
                strategy=self.name,
                stop_distance=stop,
            )

        if len(bars) < p.warmup_bars:
            return signal(
                Action.NO_TRADE,
                {"warmup_complete": False, "bars_available": len(bars), "bars_required": p.warmup_bars},
                f"Datos insuficientes para el calentamiento: hay {len(bars)} barras y se requieren "
                f"{p.warmup_bars}.",
            )

        closes = [b.close for b in bars]
        fast_now, fast_prev = sma(closes, p.fast_period), sma(closes[:-1], p.fast_period)
        slow_now, slow_prev = sma(closes, p.slow_period), sma(closes[:-1], p.slow_period)
        rsi_now = rsi(closes, p.rsi_period)
        atr_now = atr(bars, p.atr_period)
        assert None not in (fast_now, fast_prev, slow_now, slow_prev, rsi_now, atr_now)

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now
        f, s = f"SMA{p.fast_period}", f"SMA{p.slow_period}"
        conditions = {
            "warmup_complete": True,
            f"sma_fast_{p.fast_period}": fast_now,
            f"sma_fast_{p.fast_period}_prev": fast_prev,
            f"sma_slow_{p.slow_period}": slow_now,
            f"sma_slow_{p.slow_period}_prev": slow_prev,
            f"rsi_{p.rsi_period}": rsi_now,
            f"atr_{p.atr_period}": atr_now,
            "crossed_up": crossed_up,
            "crossed_down": crossed_down,
            "rsi_buy_max": p.rsi_buy_max,
            "rsi_sell_min": p.rsi_sell_min,
        }
        stop = atr_now * p.stop_atr_multiple
        cross_up_text = (
            f"{f} cruzó por encima de {s} al cierre ({f}={fast_now:.5f} > {s}={slow_now:.5f}; "
            f"barra previa {fast_prev:.5f} <= {slow_prev:.5f})"
        )
        cross_down_text = (
            f"{f} cruzó por debajo de {s} al cierre ({f}={fast_now:.5f} < {s}={slow_now:.5f}; "
            f"barra previa {fast_prev:.5f} >= {slow_prev:.5f})"
        )
        rsi_text = f"RSI{p.rsi_period}={rsi_now:.2f}"

        if crossed_up and rsi_now < p.rsi_buy_max:
            return signal(
                Action.BUY,
                conditions,
                f"{cross_up_text} y {rsi_text} < {p.rsi_buy_max}. Stop propuesto: "
                f"{p.stop_atr_multiple} x ATR{p.atr_period} = {stop:.5f}.",
                stop,
            )
        if crossed_up:
            return signal(
                Action.NO_TRADE, conditions,
                f"{cross_up_text}, pero el filtro lo descarta: {rsi_text} >= {p.rsi_buy_max}.",
            )
        if crossed_down and rsi_now > p.rsi_sell_min:
            return signal(
                Action.SELL,
                conditions,
                f"{cross_down_text} y {rsi_text} > {p.rsi_sell_min}. Stop propuesto: "
                f"{p.stop_atr_multiple} x ATR{p.atr_period} = {stop:.5f}.",
                stop,
            )
        if crossed_down:
            return signal(
                Action.NO_TRADE, conditions,
                f"{cross_down_text}, pero el filtro lo descarta: {rsi_text} <= {p.rsi_sell_min}.",
            )
        relation = ">" if fast_now > slow_now else "<" if fast_now < slow_now else "="
        return signal(
            Action.NO_TRADE,
            conditions,
            f"Sin cruce de medias en esta barra ({f}={fast_now:.5f} {relation} {s}={slow_now:.5f}; {rsi_text}).",
        )


def build_strategy(params: StrategyParams) -> Strategy:
    if params.name == SmaCrossRsiStrategy.name:
        return SmaCrossRsiStrategy(params)
    raise ConfigError(f"Estrategia desconocida '{params.name}'.")


def window_for(bars: Sequence[Bar], index: int, strategy: Strategy) -> Sequence[Bar]:
    """Historial visible al cierre de la barra ``index``: nunca incluye barras posteriores."""
    return bars[max(0, index + 1 - strategy.lookback) : index + 1]


def generate_signals(dataset: Dataset, strategy: Strategy) -> list[Signal]:
    """Señal de cada barra del conjunto, evaluada solo con su pasado."""
    return [
        strategy.evaluate(dataset.instrument, window_for(dataset.bars, i, strategy), dataset.provenance)
        for i in range(len(dataset.bars))
    ]
