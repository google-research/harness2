"""Interfaz abstracta de bróker, bróker simulado y adaptador MT5 deshabilitado.

``Broker`` define el contrato que una integración futura con MetaTrader 5 debería cumplir
(ejecutar órdenes de mercado, emular stops/objetivos del lado servidor y cerrar posiciones).
En esta versión la única implementación funcional es ``SimulatedBroker``. El adaptador
``MetaTrader5Broker`` existe solo como marcador: no importa ninguna librería de bróker, no
acepta credenciales y lanza ``LiveTradingDisabledError`` al instanciarse o usarse.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trade_agent.config import InstrumentSpec
from trade_agent.costs import commission_per_side, entry_price, exit_side_price, gross_pnl, market_exit_price
from trade_agent.domain import Bar, OrderRequest, Position, Side

SIMULATION = "SIMULATION"


class LiveTradingDisabledError(RuntimeError):
    """Se lanza ante cualquier intento de operar fuera del modo simulación."""


@dataclass(frozen=True)
class ExecutionReport:
    status: str  # FILLED | REJECTED
    client_order_id: str
    instrument: str
    side: Side
    lots: Decimal | None
    price: Decimal | None
    ts: datetime
    commission: Decimal
    reason_code: str | None = None
    message: str = ""

    @property
    def entry_price(self) -> Decimal | None:
        return self.price


@dataclass(frozen=True)
class ExitFill:
    price: Decimal
    reason: str  # STOP_LOSS | TAKE_PROFIT
    loss_beyond_plan: bool


@dataclass(frozen=True)
class CloseReport:
    exit_price: Decimal
    gross_pnl: Decimal
    commission: Decimal  # ida y vuelta
    net_pnl: Decimal


class Broker(ABC):
    mode: str

    @abstractmethod
    def execute(self, order: OrderRequest, market: Bar, spec: InstrumentSpec) -> ExecutionReport:
        """Ejecuta una orden de mercado al precio de apertura de ``market``."""

    @abstractmethod
    def check_protective_exit(self, position: Position, market: Bar, spec: InstrumentSpec) -> ExitFill | None:
        """Determina si el stop o el objetivo de ``position`` se alcanzan durante ``market``."""

    @abstractmethod
    def settle(self, position: Position | ExecutionReport, exit_price: Decimal, spec: InstrumentSpec) -> CloseReport:
        """Calcula el resultado económico de cerrar al precio ``exit_price``."""

    def close_at_market(self, position: Position | ExecutionReport, mid: Decimal, spec: InstrumentSpec) -> CloseReport:
        return self.settle(position, market_exit_price(position.side, mid, spec), spec)


class SimulatedBroker(Broker):
    """Ejecución ficticia con spread, deslizamiento y comisiones configurables.

    Idempotente por ``client_order_id``: una orden repetida se rechaza con ``DUPLICATE_ORDER``
    y no produce una segunda ejecución.
    """

    mode = SIMULATION

    def __init__(self, account_currency: str):
        self.account_currency = account_currency
        self._executed: dict[str, ExecutionReport] = {}

    @property
    def fill_count(self) -> int:
        return len(self._executed)

    def execute(self, order: OrderRequest, market: Bar, spec: InstrumentSpec) -> ExecutionReport:
        def rejected(code: str, message: str) -> ExecutionReport:
            return ExecutionReport("REJECTED", order.client_order_id, order.instrument, order.side,
                                   order.lots, None, market.timestamp, Decimal(0), code, message)

        if order.client_order_id in self._executed:
            return rejected("DUPLICATE_ORDER", f"La orden {order.client_order_id} ya se ejecutó; se ignora el duplicado.")
        if order.lots is None or order.lots <= 0:
            return rejected("INVALID_SIZE", "La orden no tiene un tamaño aprobado por el motor de riesgo.")
        report = ExecutionReport(
            "FILLED",
            order.client_order_id,
            order.instrument,
            order.side,
            order.lots,
            entry_price(order.side, market.open, spec),
            market.timestamp,
            commission_per_side(order.lots, spec),
            message="Ejecución SIMULADA (sin dinero real).",
        )
        self._executed[order.client_order_id] = report
        return report

    def check_protective_exit(self, position: Position, market: Bar, spec: InstrumentSpec) -> ExitFill | None:
        side = position.side
        slip = spec.slippage
        # Precios del lado de cierre durante la barra (bid para largos, ask para cortos).
        at_open = exit_side_price(side, market.open, spec)
        worst = exit_side_price(side, market.low if side is Side.BUY else market.high, spec)
        best = exit_side_price(side, market.high if side is Side.BUY else market.low, spec)
        sign = side.sign
        stop, target = position.stop_loss, position.take_profit
        # Orden conservador: si stop y objetivo caben en la misma barra, se asume el stop.
        if sign * (at_open - stop) < 0:
            return ExitFill(at_open - sign * slip, "STOP_LOSS", True)  # hueco a través del stop
        if sign * (worst - stop) <= 0:
            return ExitFill(stop - sign * slip, "STOP_LOSS", False)
        if sign * (at_open - target) >= 0:
            return ExitFill(at_open, "TAKE_PROFIT", False)
        if sign * (best - target) >= 0:
            return ExitFill(target, "TAKE_PROFIT", False)
        return None

    def settle(self, position: Position | ExecutionReport, exit_price: Decimal, spec: InstrumentSpec) -> CloseReport:
        fx = spec.fx_rate(self.account_currency)
        entry_commission = position.commission
        gross = gross_pnl(position.side, position.lots, position.entry_price, exit_price, spec, fx)
        commission = entry_commission + commission_per_side(position.lots, spec)
        return CloseReport(exit_price, gross, commission, gross - commission)


class MetaTrader5Broker(Broker):
    """Marcador para una integración futura con MetaTrader 5. DESHABILITADO en v0.1.

    Una versión futura debería implementar ``execute``/``check_protective_exit``/``settle``
    contra el terminal, con confirmación humana explícita y cuenta demo primero. Esta versión
    no lo hace: cualquier uso lanza ``LiveTradingDisabledError``.
    """

    mode = "LIVE_DISABLED"
    _MESSAGE = (
        "La ejecución real (MetaTrader 5) está deshabilitada técnicamente en Trade Agent 0.1: "
        "solo existe el modo simulación."
    )

    def __init__(self, *args, **kwargs):
        raise LiveTradingDisabledError(self._MESSAGE)

    def execute(self, order, market, spec):  # pragma: no cover - inalcanzable
        raise LiveTradingDisabledError(self._MESSAGE)

    def check_protective_exit(self, position, market, spec):  # pragma: no cover - inalcanzable
        raise LiveTradingDisabledError(self._MESSAGE)

    def settle(self, position, exit_price, spec):  # pragma: no cover - inalcanzable
        raise LiveTradingDisabledError(self._MESSAGE)


def create_broker(mode: str, account_currency: str) -> Broker:
    if str(mode).strip().lower() != "simulation":
        raise LiveTradingDisabledError(
            f"Modo '{mode}' no disponible: Trade Agent 0.1 solo ejecuta órdenes simuladas."
        )
    return SimulatedBroker(account_currency)
