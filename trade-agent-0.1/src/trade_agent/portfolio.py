"""Estado de la cuenta simulada: posiciones abiertas/cerradas, P/L y exposición."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from trade_agent.config import InstrumentSpec
from trade_agent.costs import exit_side_price, gross_pnl
from trade_agent.domain import Position

OPEN = "OPEN"
CLOSED = "CLOSED"


class Portfolio:
    def __init__(self, initial_capital: Decimal, account_currency: str, specs: dict[str, InstrumentSpec]):
        self.initial_capital = initial_capital
        self.currency = account_currency
        self.specs = specs
        self.executed_order_ids: set[str] = set()
        self.marks: dict[str, Decimal] = {}
        self._positions: list[Position] = []

    # --- consultas -----------------------------------------------------------------------

    def all_positions(self) -> list[Position]:
        return list(self._positions)

    def open_positions(self) -> list[Position]:
        return [p for p in self._positions if p.status == OPEN]

    def closed_positions(self) -> list[Position]:
        return [p for p in self._positions if p.status == CLOSED]

    def open_position_for(self, instrument: str) -> Position | None:
        return next((p for p in self.open_positions() if p.instrument == instrument), None)

    def realized_pnl(self) -> Decimal:
        return sum((p.net_pnl for p in self.closed_positions()), Decimal(0))

    def realized_pnl_on(self, day: date) -> Decimal:
        return sum((p.net_pnl for p in self.closed_positions() if p.exit_ts.date() == day), Decimal(0))

    def open_risk(self) -> Decimal:
        """Suma de las pérdidas máximas planificadas de las posiciones abiertas."""
        return sum((p.planned_max_loss for p in self.open_positions()), Decimal(0))

    def used_margin(self) -> Decimal:
        return sum((p.margin for p in self.open_positions()), Decimal(0))

    def unrealized_pnl(self, position: Position) -> Decimal:
        spec = self.specs[position.instrument]
        mid = self.marks.get(position.instrument, position.entry_price)
        value = exit_side_price(position.side, mid, spec)
        return gross_pnl(position.side, position.lots, position.entry_price, value, spec, spec.fx_rate(self.currency))

    def balance(self) -> Decimal:
        """Capital + P/L neto realizado - comisiones de entrada ya pagadas por posiciones abiertas."""
        paid = sum((p.commission for p in self.open_positions()), Decimal(0))
        return self.initial_capital + self.realized_pnl() - paid

    def equity(self) -> Decimal:
        return self.balance() + sum((self.unrealized_pnl(p) for p in self.open_positions()), Decimal(0))

    # --- mutaciones ----------------------------------------------------------------------

    def next_position_id(self) -> str:
        return f"P{len(self._positions) + 1:04d}"

    def mark(self, instrument: str, mid: Decimal) -> None:
        self.marks[instrument] = mid

    def add(self, position: Position) -> None:
        if position.client_order_id in self.executed_order_ids:
            raise ValueError(f"La orden {position.client_order_id} ya fue ejecutada.")
        self.executed_order_ids.add(position.client_order_id)
        self._positions.append(position)

    def close(
        self,
        position: Position,
        ts: datetime,
        price: Decimal,
        reason: str,
        gross: Decimal,
        commission_total: Decimal,
        loss_beyond_plan: bool = False,
    ) -> Position:
        if position.status != OPEN:
            raise ValueError(f"La posición {position.position_id} ya está cerrada.")
        position.status = CLOSED
        position.exit_ts = ts
        position.exit_price = price
        position.exit_reason = reason
        position.gross_pnl = gross
        position.commission = commission_total
        position.net_pnl = gross - commission_total
        position.loss_beyond_plan = loss_beyond_plan
        return position
