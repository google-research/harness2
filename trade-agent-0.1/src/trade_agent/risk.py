"""Motor de riesgo: aprueba o rechaza cada orden y calcula su tamaño.

La pérdida potencial se calcula en la DIVISA DE LA CUENTA:

    pérdida_máx = (distancia_stop + deslizamiento) x tamaño_contrato x lotes x fx
                  + 2 x comisión_por_lado x lotes

El stop se coloca a ``distancia_stop`` del precio de ejecución (ya incluye el medio spread),
así que el spread de entrada está contenido en esa distancia. El tamaño es el mayor múltiplo
de ``lot_step`` que respeta simultáneamente: pérdida máxima por operación, presupuesto
(margen) por operación, pérdida diaria restante, margen libre y lote máximo.

Reglas de rechazo: datos de coste/contrato incompletos, sin stop, stop dentro del spread,
orden duplicada, añadir a una posición perdedora, posición ya abierta en el instrumento,
máximo de posiciones simultáneas, límite de pérdida diaria y tamaño inferior al lote mínimo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from trade_agent.config import AccountConfig, InstrumentSpec, RiskLimits
from trade_agent.domain import OrderRequest, ceil_to_step, floor_to_step
from trade_agent.portfolio import Portfolio

PRE_TRADE = "PRE_TRADE"
AT_FILL = "AT_FILL"


@dataclass(frozen=True)
class RiskCheck:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    stage: str
    checks: tuple[RiskCheck, ...]
    lots: Decimal | None = None
    stop_distance: Decimal | None = None
    take_profit_distance: Decimal | None = None
    breakdown: dict = field(default_factory=dict)

    @property
    def reason_codes(self) -> list[str]:
        return [c.code for c in self.checks if not c.passed]

    @property
    def reasons(self) -> list[str]:
        return [f"{c.code}: {c.detail}" for c in self.checks if not c.passed]

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "stage": self.stage,
            "lots": self.lots,
            "stop_distance": self.stop_distance,
            "take_profit_distance": self.take_profit_distance,
            "breakdown": self.breakdown,
            "checks": [{"code": c.code, "passed": c.passed, "detail": c.detail} for c in self.checks],
            "reason_codes": self.reason_codes,
        }


class RiskEngine:
    def __init__(self, limits: RiskLimits, account: AccountConfig):
        self.limits = limits
        self.account = account

    def evaluate(
        self,
        order: OrderRequest,
        spec: InstrumentSpec | None,
        portfolio: Portfolio,
        price: Decimal,
        day: date,
        stage: str = PRE_TRADE,
    ) -> RiskDecision:
        checks: list[RiskCheck] = []
        limits = self.limits

        def reject() -> RiskDecision:
            return RiskDecision(False, stage, tuple(checks))

        # 1. Información suficiente sobre coste, spread, contrato y exposición.
        if spec is None:
            checks.append(RiskCheck(
                "MISSING_INSTRUMENT_SPEC", False,
                f"No hay especificación de contrato/costes para {order.instrument}; no se puede "
                "calcular la exposición ni la pérdida potencial.",
            ))
            return reject()
        missing = spec.missing_fields(self.account.currency)
        if missing:
            checks.append(RiskCheck(
                "MISSING_COST_INFO", False,
                f"Faltan datos para calcular coste y exposición de {order.instrument}: {', '.join(missing)}.",
            ))
            return reject()
        checks.append(RiskCheck("COST_INFO_COMPLETE", True, "Spread, comisión, contrato, margen y divisa definidos."))

        if order.stop_distance is None or order.stop_distance <= 0:
            checks.append(RiskCheck(
                "MISSING_STOP", False, "La orden no define stop: la pérdida potencial no estaría acotada."
            ))
            return reject()
        stop_distance = ceil_to_step(order.stop_distance, spec.tick_size)
        if stop_distance <= spec.spread + spec.slippage:
            checks.append(RiskCheck(
                "STOP_INSIDE_SPREAD", False,
                f"La distancia de stop {stop_distance} no supera spread + deslizamiento "
                f"({spec.spread + spec.slippage}); la orden se cerraría al ejecutarse.",
            ))
            return reject()

        # 2. Reglas de cartera.
        if order.client_order_id in portfolio.executed_order_ids:
            checks.append(RiskCheck(
                "DUPLICATE_ORDER", False, f"La orden {order.client_order_id} ya fue ejecutada; no se repite."
            ))
        existing = portfolio.open_position_for(order.instrument)
        if existing is not None:
            if existing.side == order.side and portfolio.unrealized_pnl(existing) < 0:
                checks.append(RiskCheck(
                    "ADD_TO_LOSING_POSITION", False,
                    f"Prohibido aumentar la posición perdedora {existing.position_id} en {order.instrument}.",
                ))
            else:
                checks.append(RiskCheck(
                    "POSITION_ALREADY_OPEN", False,
                    f"Ya existe la posición {existing.position_id} en {order.instrument} (máximo una por instrumento).",
                ))
        open_count = len(portfolio.open_positions())
        if open_count >= limits.max_open_positions:
            checks.append(RiskCheck(
                "MAX_OPEN_POSITIONS", False,
                f"Hay {open_count} posiciones abiertas; el máximo simultáneo es {limits.max_open_positions}.",
            ))
        loss_used = max(Decimal(0), -portfolio.realized_pnl_on(day))
        if loss_used >= limits.daily_loss_limit:
            checks.append(RiskCheck(
                "DAILY_LOSS_LIMIT_REACHED", False,
                f"Pérdida realizada del día {day.isoformat()}: {loss_used}; límite diario {limits.daily_loss_limit}.",
            ))
        if any(not c.passed for c in checks):
            return reject()

        # 3. Dimensionamiento bajo todos los límites a la vez.
        currency = self.account.currency
        fx = spec.fx_rate(currency)
        value_per_point_lot = spec.contract_size * fx
        loss_per_lot = (stop_distance + spec.slippage) * value_per_point_lot + 2 * spec.commission_per_lot_per_side
        margin_per_lot = price * value_per_point_lot * spec.margin_rate
        open_risk = portfolio.open_risk()
        remaining_daily = limits.daily_loss_limit - loss_used - open_risk
        free_margin = portfolio.equity() - portfolio.used_margin()
        caps = {
            "max_loss_per_trade": limits.max_loss_per_trade / loss_per_lot,
            "max_budget_per_trade": limits.max_budget_per_trade / margin_per_lot,
            "daily_loss_limit": max(Decimal(0), remaining_daily) / loss_per_lot,
            "free_margin": max(Decimal(0), free_margin) / margin_per_lot,
            "max_lot": spec.max_lot,
        }
        binding = min(caps, key=lambda k: caps[k])
        lots = floor_to_step(caps[binding], spec.lot_step)
        if lots < spec.min_lot:
            checks.append(RiskCheck(
                "SIZE_BELOW_MIN_LOT", False,
                f"El límite '{binding}' permite como máximo {caps[binding]:.4f} lotes, por debajo del "
                f"lote mínimo {spec.min_lot}.",
            ))
            return reject()

        price_risk = stop_distance * value_per_point_lot * lots
        slippage_cost = spec.slippage * value_per_point_lot * lots
        commission = 2 * spec.commission_per_lot_per_side * lots
        max_loss = price_risk + slippage_cost + commission
        margin = margin_per_lot * lots
        tp_distance = ceil_to_step((limits.profit_target_per_trade + commission) / (value_per_point_lot * lots), spec.tick_size)
        expected_profit = tp_distance * value_per_point_lot * lots - commission

        final = [
            ("MAX_LOSS_PER_TRADE", max_loss <= limits.max_loss_per_trade,
             f"pérdida máx {max_loss:.2f} <= {limits.max_loss_per_trade} {currency}"),
            ("MAX_BUDGET_PER_TRADE", margin <= limits.max_budget_per_trade,
             f"margen {margin:.2f} <= {limits.max_budget_per_trade} {currency}"),
            ("DAILY_LOSS_LIMIT", loss_used + open_risk + max_loss <= limits.daily_loss_limit,
             f"pérdida del día {loss_used:.2f} + riesgo abierto {open_risk:.2f} + {max_loss:.2f} <= "
             f"{limits.daily_loss_limit} {currency}"),
            ("FREE_MARGIN", margin <= free_margin, f"margen {margin:.2f} <= margen libre {free_margin:.2f}"),
            ("MAX_OPEN_POSITIONS", open_count + 1 <= limits.max_open_positions,
             f"{open_count + 1} <= {limits.max_open_positions} posiciones"),
            ("NO_DUPLICATE_NO_AVERAGING", True, "orden nueva y sin posición previa en el instrumento"),
        ]
        checks.extend(RiskCheck(code, ok, detail) for code, ok, detail in final)
        breakdown = {
            "account_currency": currency,
            "fx_quote_to_account": fx,
            "lots": lots,
            "binding_limit": binding,
            "stop_distance": stop_distance,
            "take_profit_distance": tp_distance,
            "price_risk": price_risk,
            "slippage_cost": slippage_cost,
            "commission_round_trip": commission,
            "spread_cost_round_trip": spec.spread * value_per_point_lot * lots,
            "max_loss": max_loss,
            "expected_net_profit_at_tp": expected_profit,
            "margin": margin,
            "notional": price * value_per_point_lot * lots,
            "reference_mid_price": price,
        }
        approved = all(c.passed for c in checks)
        return RiskDecision(approved, stage, tuple(checks), lots, stop_distance, tp_distance, breakdown)
