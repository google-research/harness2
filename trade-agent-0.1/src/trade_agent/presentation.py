"""Presentación en texto plano (CLI) y Markdown (informe). Sin lógica de negocio."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Sequence

from trade_agent.domain import Signal
from trade_agent.serialization import to_jsonable

BANNER = "Trade Agent 0.1 | MODO SIMULACIÓN ÚNICAMENTE: ninguna orden se envía a un bróker real."


def _m(value) -> str:
    """Importe para lectura humana (2 decimales). La bitácora conserva el valor exacto."""
    if value is None:
        return "-"
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}"


def _counts(counts: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in counts.items()) if counts else "ninguno"


def _table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    rows = [[("-" if v is None else str(v)) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(v)) for w, v in zip(widths, row)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(v.ljust(w) for v, w in zip(row, widths)) for row in rows]
    return "\n".join([line, sep, *body]) if body else line + "\n" + sep + "\n(sin registros)"


def format_observation(obs: dict) -> str:
    obs = to_jsonable(obs)
    lines = [
        f"Instrumento: {obs['instrument']}   Archivo: {obs['source']}   Barras: {obs['rows']}",
        f"Procedencia: {obs['provenance_label']} — {obs['notice']}",
        f"Rango: {obs['first_ts']} -> {obs['last_ts']}",
        f"SHA-256: {obs['sha256']}",
        f"Al cierre de la última barra ({obs['as_of']}) — {obs['as_of_note']}",
    ]
    indicator_keys = [k for k in obs if k.startswith(("sma_", "rsi_", "atr_"))]
    lines.append(
        f"  Cierre: {obs['last_close']}   Máx. periodo: {obs['period_high']}   Mín. periodo: "
        f"{obs['period_low']}   Variación del periodo: {obs['period_change_pct']}%"
    )
    lines.append("  " + "   ".join(
        f"{k.upper()}: {_fmt_num(obs[k], 2 if k.startswith('rsi_') else 5)}" for k in indicator_keys))
    lines.append(f"  Tendencia: {obs['trend']}  (regla: {obs['trend_rule']})")
    return "\n".join(lines)


def _fmt_num(value, places: int = 5) -> str:
    if value is None:
        return "-"
    return f"{Decimal(str(value)):.{places}f}"


def format_signals(signals: Sequence[Signal], include_no_trade: bool = False) -> str:
    chosen = [s for s in signals if include_no_trade or s.action.value != "NO_TRADE"]
    rows = [(s.timestamp.isoformat(), s.instrument, s.action.value, s.reference_price, s.justification)
            for s in chosen]
    return _table(["Barra (cierre)", "Instrumento", "Señal", "Precio ref.", "Justificación"], rows)


def format_signal_counts(signals: Sequence[Signal]) -> str:
    counts = {a: 0 for a in ("BUY", "SELL", "NO_TRADE")}
    for s in signals:
        counts[s.action.value] += 1
    return "Señales: " + ", ".join(f"{k}={v}" for k, v in counts.items())


def format_positions(positions: Sequence[dict], status: str = "all") -> str:
    chosen = [p for p in positions if status == "all" or p["status"] == status.upper()]
    rows = [
        (p["position_id"], p["status"], p["instrument"], p["side"], p["lots"], p["entry_ts"], p["entry_price"],
         p["stop_loss"], p["take_profit"], p.get("exit_ts"), p.get("exit_price"), p.get("exit_reason"),
         _m(p.get("net_pnl")))
        for p in chosen
    ]
    headers = ["ID", "Estado", "Instr.", "Lado", "Lotes", "Entrada", "Precio ent.", "Stop", "Objetivo",
               "Salida", "Precio sal.", "Motivo", "P/L neto"]
    return _table(headers, rows)


def describe_event(e: dict) -> str:
    t = e["type"]
    if t == "SIGNAL":
        return f"{e['action']} @ {e['reference_price']} — {e['justification']}"
    if t == "RISK_CHECK":
        verdict = "APROBADA" if e["approved"] else "RECHAZADA " + ",".join(e["reason_codes"])
        b = e.get("breakdown") or {}
        extra = f" lotes={e['lots']} pérdida_máx={_m(b.get('max_loss'))} margen={_m(b.get('margin'))}" if e["approved"] else ""
        return f"[{e['stage']}] {e['client_order_id']} {verdict}{extra}"
    if t == "ORDER_PENDING":
        return f"{e['client_order_id']} PENDIENTE: {e['execution']}"
    if t == "ORDER_REJECTED":
        return f"{e['client_order_id']} RECHAZADA [{e['stage']}]: " + " | ".join(e["reasons"])
    if t == "ORDER_FILLED":
        return (f"{e['client_order_id']} EJECUTADA (SIMULADA) {e['side']} {e['lots']} lotes @ {e['fill_price']}"
                f" stop={e['stop_loss']} objetivo={e['take_profit']} comisión={_m(e['commission'])}")
    if t == "ORDER_CANCELLED":
        return f"{e['client_order_id']} CANCELADA: {e['reason']}"
    if t in ("POSITION_OPENED", "POSITION_CLOSED"):
        p = e["position"]
        if t == "POSITION_OPENED":
            return f"{p['position_id']} abierta {p['side']} {p['lots']} @ {p['entry_price']}"
        return (f"{p['position_id']} cerrada por {p['exit_reason']} @ {p['exit_price']}: bruto={_m(p['gross_pnl'])} "
                f"comisiones={_m(p['commission'])} neto={_m(p['net_pnl'])}")
    if t == "DATA_LOADED":
        return f"{e['instrument']} {e['rows']} barras {e['provenance_label']} ({e['source']})"
    if t == "SYSTEM_STOPPED":
        return f"{e['reason']} Barras procesadas: {e['bars_processed']}"
    if t == "RUN_COMPLETED":
        return f"estado={e['status']} barras={e['bars_processed']} neto={_m(e['net_pnl'])}"
    if t == "RUN_STARTED":
        return f"run_id={e['run_id']} modo={e['mode']}"
    return ""


def format_history(events: Sequence[dict], types: set[str] | None = None, limit: int | None = None) -> str:
    chosen = [e for e in events if types is None or e["type"] in types]
    if limit is not None:
        chosen = chosen[-limit:]
    lines = [f"#{e['seq']:<5} {e.get('bar_ts') or '-':<25} {e['type']:<16} {describe_event(e)}" for e in chosen]
    return "\n".join(lines) if lines else "(sin eventos)"


def format_report_summary(report: dict) -> str:
    acc, tot, pos, orders = report["account"], report["totals"], report["positions"], report["orders"]
    cur = acc["currency"]
    lines = [
        f"Corrida {report['run_id']} — {report['status']} — modo {report['mode']}",
        report["disclaimer"],
        f"Señales: BUY={report['signals']['BUY']} SELL={report['signals']['SELL']} NO_TRADE={report['signals']['NO_TRADE']}",
        f"Órdenes: pendientes creadas={orders['pending_created']} ejecutadas (simuladas)={orders['filled_simulated']} "
        f"rechazadas={orders['rejected']} canceladas={orders['cancelled']}",
        f"Rechazos por motivo: {_counts(orders['rejections_by_reason'])}",
        f"Posiciones: cerradas={pos['closed']} (ganadoras={pos['wins']}, perdedoras={pos['losses']}) abiertas={pos['open']}",
        f"P/L bruto={_m(tot['gross_pnl'])} {cur}  comisiones={_m(tot['commissions'])} {cur}  "
        f"P/L neto={_m(tot['net_pnl'])} {cur}  (spread incluido en precios: {_m(tot['spread_cost'])} {cur})",
        f"P/L no realizado de posiciones abiertas: {_m(tot['unrealized_pnl_open'])} {cur}",
        f"Capital inicial={_m(acc['initial_capital'])}  balance final={_m(acc['final_balance'])}  "
        f"equity final={_m(acc['final_equity'])} {cur}  drawdown máx.={_m(tot['max_drawdown'])} {cur}",
    ]
    return "\n".join(lines)


def render_report_markdown(report: dict) -> str:
    acc, tot, pos, orders = report["account"], report["totals"], report["positions"], report["orders"]
    cur = acc["currency"]
    out = [
        f"# Informe de simulación — Trade Agent 0.1",
        "",
        f"> **MODO SIMULACIÓN ÚNICAMENTE.** {report['disclaimer']}",
        "",
        f"- Corrida: `{report['run_id']}` — estado **{report['status']}**",
        "",
        "## Datos",
        "",
        "| Instrumento | Procedencia | Archivo | Barras | Desde | Hasta | SHA-256 |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in report["data"]:
        out.append(f"| {d['instrument']} | **{d['provenance_label']}** | {d['source']} | {d['rows']} | "
                   f"{d['first_ts']} | {d['last_ts']} | `{d['sha256'][:16]}…` |")
    out += ["", *[f"> {d['notice']}" for d in report["data"]], ""]
    limits = report["risk_limits"]
    out += [
        "## Límites de riesgo aplicados",
        "",
        f"- Capital simulado: {_m(acc['initial_capital'])} {cur}",
        f"- Presupuesto (margen) máximo por operación: {_m(limits['max_budget_per_trade'])} {cur}",
        f"- Pérdida máxima por operación: {_m(limits['max_loss_per_trade'])} {cur}",
        f"- Objetivo de beneficio neto por operación: {_m(limits['profit_target_per_trade'])} {cur}",
        f"- Límite de pérdida diaria: {_m(limits['daily_loss_limit'])} {cur}",
        f"- Máximo de posiciones simultáneas: {limits['max_open_positions']}",
        "",
        "## Actividad",
        "",
        f"- Señales: BUY={report['signals']['BUY']}, SELL={report['signals']['SELL']}, NO_TRADE={report['signals']['NO_TRADE']}",
        f"- Órdenes pendientes creadas: {orders['pending_created']}",
        f"- Órdenes ejecutadas (simuladas): {orders['filled_simulated']}",
        f"- Órdenes rechazadas: {orders['rejected']} — por motivo: {_counts(orders['rejections_by_reason'])}",
        f"- Órdenes canceladas: {orders['cancelled']}",
        "",
        "## Resultado económico (neto de costes)",
        "",
        "| Concepto | Importe |",
        "|---|---|",
        f"| P/L bruto realizado | {_m(tot['gross_pnl'])} {cur} |",
        f"| Comisiones | {_m(tot['commissions'])} {cur} |",
        f"| **P/L neto realizado** | **{_m(tot['net_pnl'])} {cur}** |",
        f"| Spread (ya incluido en precios) | {_m(tot['spread_cost'])} {cur} |",
        f"| P/L no realizado (abiertas) | {_m(tot['unrealized_pnl_open'])} {cur} |",
        f"| Mayor pérdida / mayor ganancia | {_m(tot['largest_loss'])} / {_m(tot['largest_win'])} {cur} |",
        f"| Drawdown máximo (equity) | {_m(tot['max_drawdown'])} {cur} |",
        f"| Balance final / equity final | {_m(acc['final_balance'])} / {_m(acc['final_equity'])} {cur} |",
        "",
        "## Posiciones",
        "",
        f"- Cerradas: {pos['closed']} (ganadoras {pos['wins']}, perdedoras {pos['losses']}); "
        f"motivos de salida: {_counts(pos['exit_reasons'])}",
        f"- Abiertas al final: {pos['open']}",
        f"- Pérdidas que excedieron el plan por huecos de precio: {pos['losses_beyond_plan']}",
        "",
    ]
    if report["open_positions"]:
        out += ["| ID | Instr. | Lado | Lotes | Entrada | Stop | Objetivo | Precio medio marcado | P/L no realizado |",
                "|---|---|---|---|---|---|---|---|---|"]
        for p in report["open_positions"]:
            out.append(f"| {p['position_id']} | {p['instrument']} | {p['side']} | {p['lots']} | {p['entry_price']} | "
                       f"{p['stop_loss']} | {p['take_profit']} | {p['mark_mid_price']} | {_m(p['unrealized_gross_pnl'])} {cur} |")
        out.append("")
    out += [
        "La bitácora completa y auditable está en `journal.jsonl` (un evento por línea).",
        "",
    ]
    return "\n".join(out)
