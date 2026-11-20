"""Modelo de costes y precios de ejecución simulados.

Convención: las barras OHLC representan precios MEDIOS. Una compra se ejecuta a
``medio + spread/2 + deslizamiento`` y una venta a ``medio - spread/2 - deslizamiento``.
Una posición larga se valora (y se cierra) por el lado comprador (bid = medio - spread/2) y
una corta por el lado vendedor (ask = medio + spread/2). Todos los importes se expresan en la
divisa de la cuenta mediante ``fx`` (divisa de cotización -> divisa de cuenta).
"""

from __future__ import annotations

from decimal import Decimal

from trade_agent.config import InstrumentSpec
from trade_agent.domain import Side


def entry_price(side: Side, mid: Decimal, spec: InstrumentSpec) -> Decimal:
    return mid + side.sign * (spec.half_spread + spec.slippage)


def exit_side_price(side: Side, mid: Decimal, spec: InstrumentSpec) -> Decimal:
    """Precio al que se valora la posición (sin deslizamiento): bid para largos, ask para cortos."""
    return mid - side.sign * spec.half_spread


def market_exit_price(side: Side, mid: Decimal, spec: InstrumentSpec) -> Decimal:
    return exit_side_price(side, mid, spec) - side.sign * spec.slippage


def gross_pnl(side: Side, lots: Decimal, entry: Decimal, exit_: Decimal, spec: InstrumentSpec, fx: Decimal) -> Decimal:
    return side.sign * (exit_ - entry) * spec.contract_size * lots * fx


def commission_per_side(lots: Decimal, spec: InstrumentSpec) -> Decimal:
    return spec.commission_per_lot_per_side * lots


def spread_cost(lots: Decimal, spec: InstrumentSpec, fx: Decimal) -> Decimal:
    """Coste del spread de ida y vuelta, ya incluido en los precios de ejecución (informativo)."""
    return spec.spread * spec.contract_size * lots * fx
