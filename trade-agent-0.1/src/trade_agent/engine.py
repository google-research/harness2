"""Orquestador de la simulación: datos -> señal -> validación -> orden ficticia -> cierre.

Secuencia por barra (instrumentos intercalados por timestamp y símbolo):

1. Apertura de la barra: se ejecuta la orden pendiente creada al cierre de la barra anterior,
   tras una segunda validación de riesgo con el precio de apertura (único precio conocido).
2. Durante la barra: se comprueban stop y objetivo de las posiciones abiertas (si ambos caben
   en la barra se asume el stop: criterio conservador).
3. Cierre de la barra: la estrategia evalúa SOLO el historial hasta esta barra; si hay señal,
   el motor de riesgo hace la validación previa y la orden queda PENDIENTE para la barra
   siguiente. Así ninguna decisión usa información futura.

El diario (``events``) no contiene horas de reloj ni rutas absolutas: con los mismos datos y
la misma configuración produce exactamente los mismos bytes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Callable, Sequence

from trade_agent import __version__
from trade_agent.broker import SIMULATION, Broker, LiveTradingDisabledError, create_broker
from trade_agent.config import AppConfig, ConfigError
from trade_agent.data.ingest import Dataset
from trade_agent.domain import Action, OrderRequest, Position, Side, Signal
from trade_agent.portfolio import Portfolio
from trade_agent.reporting import build_report
from trade_agent.risk import AT_FILL, PRE_TRADE, RiskEngine
from trade_agent.serialization import canonical_json, to_jsonable
from trade_agent.strategy import build_strategy, window_for


@dataclass
class SimulationResult:
    run_id: str
    status: str
    events: list[dict]
    portfolio: Portfolio
    report: dict
    manifest: dict


class Journal:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event_type: str, **fields) -> dict:
        event = {"seq": len(self.events) + 1, "type": event_type, **to_jsonable(fields)}
        self.events.append(event)
        return event

    def sha256(self) -> str:
        payload = "".join(canonical_json(e) + "\n" for e in self.events)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def client_order_id(instrument: str, signal_ts: datetime, side: Side) -> str:
    """Identificador determinista: una misma señal siempre produce el mismo id (idempotencia)."""
    return f"{instrument}-{signal_ts.strftime('%Y%m%dT%H%M%SZ')}-{side.value}"


def compute_run_id(config: AppConfig, datasets: Sequence[Dataset], options: dict) -> str:
    payload = canonical_json(
        {
            "version": __version__,
            "config": config.to_dict(),
            "datasets": [(d.instrument, d.provenance.value, d.sha256) for d in datasets],
            "options": options,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class SimulationEngine:
    def __init__(self, config: AppConfig, broker: Broker | None = None):
        self.config = config
        self.broker = broker or create_broker(config.mode, config.account.currency)
        if self.broker.mode != SIMULATION:
            raise LiveTradingDisabledError("El motor solo acepta brókers en modo SIMULATION.")
        self.strategy = build_strategy(config.strategy)
        self.risk = RiskEngine(config.risk, config.account)

    def run(
        self,
        datasets: Sequence[Dataset],
        should_stop: Callable[[], bool] | None = None,
        stop_after_bars: int | None = None,
        close_at_end: bool | None = None,
    ) -> SimulationResult:
        config = self.config
        if close_at_end is None:
            close_at_end = config.simulation.close_open_positions_at_end
        datasets = list(datasets)
        if not datasets:
            raise ConfigError("No se cargó ningún conjunto de datos.")
        instruments = [d.instrument for d in datasets]
        if len(set(instruments)) != len(instruments):
            raise ConfigError(f"Instrumentos repetidos entre los conjuntos de datos: {instruments}.")

        options = {"close_at_end": close_at_end, "stop_after_bars": stop_after_bars}
        run_id = compute_run_id(config, datasets, options)
        portfolio = Portfolio(config.account.initial_capital, config.account.currency, config.instruments)
        journal = Journal()
        journal.emit(
            "RUN_STARTED",
            run_id=run_id,
            mode=SIMULATION,
            version=__version__,
            strategy=config.to_dict()["strategy"],
            risk_limits=config.to_dict()["risk"],
            options=options,
            notice="Simulación con órdenes ficticias; ningún resultado implica rendimiento futuro.",
        )
        for dataset in datasets:
            journal.emit("DATA_LOADED", **dataset.describe())

        by_instrument = {d.instrument: d for d in datasets}
        timeline = sorted((bar.timestamp, d.instrument, i) for d in datasets for i, bar in enumerate(d.bars))
        pending: dict[str, OrderRequest] = {}
        processed = 0
        status = "COMPLETED"
        last_ctx: dict | None = None
        max_drawdown = Decimal(0)
        peak = portfolio.equity()

        for ts, instrument, index in timeline:
            if (should_stop is not None and should_stop()) or (
                stop_after_bars is not None and processed >= stop_after_bars
            ):
                status = "STOPPED"
                break
            dataset = by_instrument[instrument]
            bar = dataset.bars[index]
            spec = config.instruments.get(instrument)
            ctx = {"bar_ts": ts, "bar_index": index, "instrument": instrument}

            portfolio.mark(instrument, bar.open)
            if instrument in pending:
                self._fill(pending.pop(instrument), bar, spec, portfolio, journal, ctx)
            for position in [p for p in portfolio.open_positions() if p.instrument == instrument]:
                exit_fill = self.broker.check_protective_exit(position, bar, spec)
                if exit_fill is not None:
                    self._close(position, bar.timestamp, exit_fill.price, exit_fill.reason,
                                exit_fill.loss_beyond_plan, spec, portfolio, journal, ctx)
            portfolio.mark(instrument, bar.close)

            signal = self.strategy.evaluate(instrument, window_for(dataset.bars, index, self.strategy), dataset.provenance)
            journal.emit("SIGNAL", **{**ctx, **signal.to_dict()})
            if signal.action is not Action.NO_TRADE:
                self._propose(signal, bar.close, spec, portfolio, pending, journal, ctx)

            equity = portfolio.equity()
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            processed += 1
            last_ctx = ctx

        if status == "STOPPED":
            journal.emit(
                "SYSTEM_STOPPED",
                bar_ts=last_ctx["bar_ts"] if last_ctx else None,
                reason="Detención solicitada por el operador.",
                bars_processed=processed,
                open_positions=[p.position_id for p in portfolio.open_positions()],
            )
            for instrument, order in sorted(pending.items()):
                journal.emit("ORDER_CANCELLED", bar_ts=last_ctx["bar_ts"] if last_ctx else None,
                             instrument=instrument, client_order_id=order.client_order_id,
                             status="CANCELLED", reason="SYSTEM_STOPPED")
        else:
            for instrument, order in sorted(pending.items()):
                last = by_instrument[instrument].bars[-1]
                journal.emit("ORDER_CANCELLED", bar_ts=last.timestamp, instrument=instrument,
                             client_order_id=order.client_order_id, status="CANCELLED",
                             reason="NO_NEXT_BAR: no hay barra siguiente para ejecutar la orden.")
            if close_at_end:
                for position in portfolio.open_positions():
                    dataset = by_instrument[position.instrument]
                    last = dataset.bars[-1]
                    spec = config.instruments[position.instrument]
                    ctx = {"bar_ts": last.timestamp, "bar_index": len(dataset.bars) - 1,
                           "instrument": position.instrument}
                    price = self.broker.close_at_market(position, last.close, spec).exit_price
                    self._close(position, last.timestamp, price, "END_OF_DATA", False, spec, portfolio, journal, ctx)
            final_equity = portfolio.equity()
            max_drawdown = max(max_drawdown, peak - final_equity)

        report = build_report(run_id, status, portfolio, journal.events, datasets, config, max_drawdown)
        journal.emit(
            "RUN_COMPLETED",
            status=status,
            bars_processed=processed,
            open_positions=report["positions"]["open"],
            closed_positions=report["positions"]["closed"],
            net_pnl=report["totals"]["net_pnl"],
        )
        manifest = {
            "run_id": run_id,
            "version": __version__,
            "mode": SIMULATION,
            "status": status,
            "config": config.to_dict(),
            "datasets": [to_jsonable(d.describe()) for d in datasets],
            "options": options,
            "event_count": len(journal.events),
            "journal_sha256": journal.sha256(),
        }
        return SimulationResult(run_id, status, journal.events, portfolio, report, manifest)

    # --- pasos ---------------------------------------------------------------------------

    def _propose(self, signal: Signal, close_price: Decimal, spec, portfolio, pending, journal, ctx) -> None:
        side = Side(signal.action.value)
        order = OrderRequest(
            client_order_id=client_order_id(signal.instrument, signal.timestamp, side),
            instrument=signal.instrument,
            side=side,
            signal_ts=signal.timestamp,
            reference_price=signal.reference_price,
            stop_distance=signal.stop_distance,
        )
        pending_ids = {o.client_order_id for o in pending.values()}
        if order.client_order_id in pending_ids or order.client_order_id in portfolio.executed_order_ids:
            journal.emit("ORDER_REJECTED", **ctx, client_order_id=order.client_order_id, side=side,
                         stage=PRE_TRADE, status="REJECTED", reason_codes=["DUPLICATE_ORDER"],
                         reasons=["DUPLICATE_ORDER: la orden ya existe; no se crea otra."])
            return
        decision = self.risk.evaluate(order, spec, portfolio, close_price, ctx["bar_ts"].date(), PRE_TRADE)
        journal.emit("RISK_CHECK", **ctx, client_order_id=order.client_order_id, **decision.to_dict())
        if decision.approved:
            pending[order.instrument] = order
            journal.emit("ORDER_PENDING", **ctx, client_order_id=order.client_order_id, side=side,
                         status="PENDING", reference_price=order.reference_price,
                         stop_distance=decision.stop_distance, preliminary_lots=decision.lots,
                         execution="Mercado, en la apertura de la barra siguiente (simulado).")
        else:
            journal.emit("ORDER_REJECTED", **ctx, client_order_id=order.client_order_id, side=side,
                         stage=PRE_TRADE, status="REJECTED", reason_codes=decision.reason_codes,
                         reasons=decision.reasons)

    def _fill(self, order: OrderRequest, bar, spec, portfolio: Portfolio, journal: Journal, ctx) -> None:
        decision = self.risk.evaluate(order, spec, portfolio, bar.open, bar.timestamp.date(), AT_FILL)
        journal.emit("RISK_CHECK", **ctx, client_order_id=order.client_order_id, **decision.to_dict())
        if not decision.approved:
            journal.emit("ORDER_REJECTED", **ctx, client_order_id=order.client_order_id, side=order.side,
                         stage=AT_FILL, status="REJECTED", reason_codes=decision.reason_codes,
                         reasons=decision.reasons)
            return
        sized = replace(order, lots=decision.lots, stop_distance=decision.stop_distance)
        execution = self.broker.execute(sized, bar, spec)
        if execution.status != "FILLED":
            journal.emit("ORDER_REJECTED", **ctx, client_order_id=order.client_order_id, side=order.side,
                         stage=AT_FILL, status="REJECTED", reason_codes=[execution.reason_code],
                         reasons=[f"{execution.reason_code}: {execution.message}"])
            return
        sign = order.side.sign
        breakdown = decision.breakdown
        position = Position(
            position_id=portfolio.next_position_id(),
            client_order_id=order.client_order_id,
            instrument=order.instrument,
            side=order.side,
            lots=execution.lots,
            entry_ts=bar.timestamp,
            entry_price=execution.price,
            stop_loss=execution.price - sign * decision.stop_distance,
            take_profit=execution.price + sign * decision.take_profit_distance,
            planned_max_loss=breakdown["max_loss"],
            margin=breakdown["margin"],
            notional=breakdown["notional"],
            commission=execution.commission,
            spread_cost=breakdown["spread_cost_round_trip"],
            extra={"signal_ts": order.signal_ts},
        )
        portfolio.add(position)
        journal.emit("ORDER_FILLED", **ctx, client_order_id=order.client_order_id, side=order.side,
                     status="FILLED_SIMULATED", simulated=True, lots=execution.lots,
                     fill_price=execution.price, commission=execution.commission,
                     stop_loss=position.stop_loss, take_profit=position.take_profit, risk=breakdown,
                     open_positions_after=len(portfolio.open_positions()))
        journal.emit("POSITION_OPENED", **ctx, position=position.to_dict())

    def _close(self, position: Position, ts, price, reason, beyond_plan, spec, portfolio, journal, ctx) -> None:
        settlement = self.broker.settle(position, price, spec)
        portfolio.close(position, ts, price, reason, settlement.gross_pnl, settlement.commission, beyond_plan)
        journal.emit("POSITION_CLOSED", **ctx, reason=reason, position=position.to_dict())


def run_simulation(
    datasets: Sequence[Dataset],
    config: AppConfig,
    should_stop: Callable[[], bool] | None = None,
    stop_after_bars: int | None = None,
    close_at_end: bool | None = None,
    broker: Broker | None = None,
) -> SimulationResult:
    return SimulationEngine(config, broker).run(datasets, should_stop, stop_after_bars, close_at_end)
