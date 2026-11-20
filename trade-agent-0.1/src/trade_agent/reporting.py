"""Informe de una simulación (dict serializable, sin horas de reloj)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Sequence

from trade_agent.config import AppConfig
from trade_agent.data.ingest import Dataset, Provenance
from trade_agent.portfolio import Portfolio
from trade_agent.serialization import to_jsonable

DISCLAIMER = (
    "Resultados de una SIMULACIÓN con órdenes ficticias sobre datos {labels}. No son cotizaciones "
    "actuales, no constituyen asesoría financiera y no anticipan resultados futuros."
)


def build_report(
    run_id: str,
    status: str,
    portfolio: Portfolio,
    events: Sequence[dict],
    datasets: Sequence[Dataset],
    config: AppConfig,
    max_drawdown: Decimal,
) -> dict:
    closed = portfolio.closed_positions()
    open_positions = portfolio.open_positions()
    signals = Counter(e["action"] for e in events if e["type"] == "SIGNAL")
    rejections = Counter(
        code for e in events if e["type"] == "ORDER_REJECTED" for code in e["reason_codes"]
    )
    by_type = Counter(e["type"] for e in events)
    wins = [p for p in closed if p.net_pnl > 0]
    losses = [p for p in closed if p.net_pnl <= 0]
    gross = sum((p.gross_pnl for p in closed), Decimal(0))
    commissions = sum((p.commission for p in closed), Decimal(0))
    unrealized = sum((portfolio.unrealized_pnl(p) for p in open_positions), Decimal(0))
    labels = " y ".join(sorted({d.provenance.label for d in datasets}))
    report = {
        "run_id": run_id,
        "mode": "SIMULATION",
        "status": status,
        "disclaimer": DISCLAIMER.format(labels=labels),
        "synthetic_data": any(d.provenance is Provenance.SYNTHETIC for d in datasets),
        "data": [
            {k: v for k, v in d.describe().items() if k in
             ("instrument", "provenance", "provenance_label", "notice", "source", "rows", "first_ts", "last_ts", "sha256")}
            for d in datasets
        ],
        "account": {
            "currency": config.account.currency,
            "initial_capital": config.account.initial_capital,
            "final_balance": portfolio.balance(),
            "final_equity": portfolio.equity(),
        },
        "risk_limits": config.to_dict()["risk"],
        "signals": {action: signals.get(action, 0) for action in ("BUY", "SELL", "NO_TRADE")},
        "orders": {
            "pending_created": by_type.get("ORDER_PENDING", 0),
            "filled_simulated": by_type.get("ORDER_FILLED", 0),
            "rejected": by_type.get("ORDER_REJECTED", 0),
            "cancelled": by_type.get("ORDER_CANCELLED", 0),
            "rejections_by_reason": dict(sorted(rejections.items())),
        },
        "positions": {
            "open": len(open_positions),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (Decimal(len(wins)) / len(closed)).quantize(Decimal("0.0001")) if closed else None,
            "exit_reasons": dict(sorted(Counter(p.exit_reason for p in closed).items())),
            "losses_beyond_plan": sum(1 for p in closed if p.loss_beyond_plan),
        },
        "totals": {
            "gross_pnl": gross,
            "commissions": commissions,
            "net_pnl": gross - commissions,
            "spread_cost": sum((p.spread_cost for p in closed), Decimal(0)),
            "unrealized_pnl_open": unrealized,
            "largest_loss": min((p.net_pnl for p in closed), default=Decimal(0)),
            "largest_win": max((p.net_pnl for p in closed), default=Decimal(0)),
            "max_drawdown": max_drawdown,
        },
        "open_positions": [
            {**p.to_dict(), "unrealized_gross_pnl": portfolio.unrealized_pnl(p),
             "mark_mid_price": portfolio.marks.get(p.instrument)}
            for p in open_positions
        ],
    }
    return to_jsonable(report)
