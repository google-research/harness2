"""Tipos de dominio compartidos por todas las capas.

Todo valor monetario o de precio es ``Decimal`` para que los cálculos sean exactos y
reproducibles; nunca se usan ``float`` en decisiones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class Bar:
    """Barra OHLCV. ``timestamp`` es la hora de apertura de la barra, en UTC."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Signal:
    """Resultado de evaluar la estrategia al cierre de la barra ``timestamp``."""

    instrument: str
    timestamp: datetime
    reference_price: Decimal
    action: Action
    conditions: dict[str, Any]
    justification: str
    data: dict[str, Any]
    strategy: str
    stop_distance: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "signal_ts": self.timestamp,
            "reference_price": self.reference_price,
            "action": self.action.value,
            "conditions": self.conditions,
            "justification": self.justification,
            "data": self.data,
            "strategy": self.strategy,
            "stop_distance": self.stop_distance,
        }


@dataclass(frozen=True)
class OrderRequest:
    """Orden de mercado ficticia que se ejecutaría en la apertura de la barra siguiente."""

    client_order_id: str
    instrument: str
    side: Side
    signal_ts: datetime
    reference_price: Decimal
    stop_distance: Decimal | None
    lots: Decimal | None = None


@dataclass
class Position:
    position_id: str
    client_order_id: str
    instrument: str
    side: Side
    lots: Decimal
    entry_ts: datetime
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    planned_max_loss: Decimal
    margin: Decimal
    notional: Decimal
    commission: Decimal
    spread_cost: Decimal
    status: str = "OPEN"
    exit_ts: datetime | None = None
    exit_price: Decimal | None = None
    exit_reason: str | None = None
    gross_pnl: Decimal | None = None
    net_pnl: Decimal | None = None
    loss_beyond_plan: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "client_order_id": self.client_order_id,
            "instrument": self.instrument,
            "side": self.side.value,
            "lots": self.lots,
            "status": self.status,
            "entry_ts": self.entry_ts,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "planned_max_loss": self.planned_max_loss,
            "margin": self.margin,
            "notional": self.notional,
            "commission": self.commission,
            "spread_cost": self.spread_cost,
            "exit_ts": self.exit_ts,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "loss_beyond_plan": self.loss_beyond_plan,
            **self.extra,
        }


# --- utilidades numéricas --------------------------------------------------------------


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Redondea hacia abajo al múltiplo de ``step`` (nunca agranda una posición)."""
    if value <= 0:
        return Decimal(0) * step
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Redondea hacia arriba al múltiplo de ``step``."""
    return (value / step).to_integral_value(rounding=ROUND_UP) * step
