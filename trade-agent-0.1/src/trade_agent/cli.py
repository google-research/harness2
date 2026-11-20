"""Interfaz de línea de comandos y shell interactivo de Trade Agent 0.1 (solo simulación).

Subcomandos: load, analyze, simulate, positions, history, report, generate-synthetic, demo,
shell. Códigos de salida: 0 correcto, 2 datos/configuración inválidos, 3 modo real solicitado.
"""

from __future__ import annotations

import argparse
import cmd
import platform
import shlex
import signal as signal_module
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence, TextIO

from trade_agent import __version__
from trade_agent.broker import LiveTradingDisabledError
from trade_agent.config import AppConfig, ConfigError, default_config, load_config
from trade_agent.data.ingest import Dataset, DataValidationError, load_csv
from trade_agent.data.synthetic import write_synthetic_csv
from trade_agent.engine import SimulationResult, run_simulation
from trade_agent.observer import observe
from trade_agent.persistence import RunStoreError, load_run, write_run, write_run_meta
from trade_agent.presentation import (
    BANNER,
    format_history,
    format_observation,
    format_positions,
    format_report_summary,
    format_signal_counts,
    format_signals,
)
from trade_agent.serialization import pretty_json, to_jsonable
from trade_agent.strategy import build_strategy, generate_signals

EXIT_OK, EXIT_INVALID, EXIT_LIVE = 0, 2, 3


# --- servicios compartidos por subcomandos y shell -------------------------------------


def resolve_config(path: str | None) -> AppConfig:
    return load_config(path) if path else default_config()


def run_and_store(datasets: Sequence[Dataset], config: AppConfig, out_root: Path, *, close_at_end=None,
                  stop_after_bars=None, should_stop=None, argv=None) -> tuple[SimulationResult, Path]:
    started = time.perf_counter()
    started_at = datetime.now(UTC)
    result = run_simulation(datasets, config, should_stop=should_stop, stop_after_bars=stop_after_bars,
                            close_at_end=close_at_end)
    run_dir = write_run(result, Path(out_root) / result.run_id)
    write_run_meta(run_dir, {
        "note": "Único archivo no determinista de la corrida (hora de reloj y entorno).",
        "started_at_utc": started_at.isoformat(),
        "duration_seconds": round(time.perf_counter() - started, 4),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "argv": list(argv or []),
    })
    return result, run_dir


def _datasets_from_args(args) -> list[Dataset]:
    return [load_csv(path, instrument=args.instrument, provenance=args.provenance) for path in args.csv]


# --- subcomandos -----------------------------------------------------------------------


def cmd_load(args, out: TextIO) -> int:
    config = resolve_config(args.config)
    for dataset in _datasets_from_args(args):
        print("Datos válidos.", file=out)
        print(format_observation(observe(dataset, config.strategy)), file=out)
    return EXIT_OK


def cmd_analyze(args, out: TextIO) -> int:
    config = resolve_config(args.config)
    strategy = build_strategy(config.strategy)
    for dataset in _datasets_from_args(args):
        print(format_observation(observe(dataset, config.strategy)), file=out)
        signals = generate_signals(dataset, strategy)
        print(format_signal_counts(signals), file=out)
        print(format_signals(signals, include_no_trade=args.all), file=out)
    return EXIT_OK


def cmd_simulate(args, out: TextIO) -> int:
    config = resolve_config(args.config)
    datasets = _datasets_from_args(args)
    stop_requested = {"flag": False}

    def on_interrupt(signum, frame):  # Ctrl+C -> detención ordenada, queda registrada
        stop_requested["flag"] = True

    previous = signal_module.signal(signal_module.SIGINT, on_interrupt)
    try:
        result, run_dir = run_and_store(
            datasets, config, Path(args.out),
            close_at_end=True if args.close_at_end else None,
            stop_after_bars=args.stop_after_bars,
            should_stop=lambda: stop_requested["flag"],
            argv=sys.argv,
        )
    finally:
        signal_module.signal(signal_module.SIGINT, previous)
    print(format_report_summary(result.report), file=out)
    print(f"Corrida guardada en: {run_dir}", file=out)
    return EXIT_OK


def cmd_positions(args, out: TextIO) -> int:
    run = load_run(args.run_dir)
    print(format_positions(run.positions, args.status), file=out)
    return EXIT_OK


def cmd_history(args, out: TextIO) -> int:
    run = load_run(args.run_dir)
    types = {t.upper() for t in args.type} if args.type else None
    print(format_history(run.events, types, args.limit), file=out)
    return EXIT_OK


def cmd_report(args, out: TextIO) -> int:
    run = load_run(args.run_dir)
    if args.markdown:
        print((run.path / "report.md").read_text("utf-8"), file=out)
    else:
        print(format_report_summary(run.report), file=out)
    return EXIT_OK


def cmd_generate(args, out: TextIO) -> int:
    path = write_synthetic_csv(args.output, instrument=args.instrument, n_bars=args.bars, seed=args.seed)
    print(f"Datos SINTÉTICOS escritos en {path} (procedencia declarada en {path.with_suffix('.meta.json').name}).",
          file=out)
    return EXIT_OK


def cmd_demo(args, out: TextIO) -> int:
    root = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="trade-agent-demo-"))
    config = resolve_config(args.config)

    def section(title: str) -> None:
        print(f"\n=== {title} ===", file=out)

    section("1. Datos")
    csv_path = write_synthetic_csv(root / "data" / "synthetic_eurusd_h1.csv", n_bars=args.bars, seed=args.seed)
    dataset = load_csv(csv_path)
    print(format_observation(observe(dataset, config.strategy)), file=out)

    section("2. Rechazo de datos inválidos (comprobación)")
    bad = root / "data" / "invalid_example.csv"
    bad.write_text("timestamp,open,high,low,close,volume\n"
                   "2020-01-01T01:00:00Z,1.1,1.2,1.0,1.1,10\n"
                   "2020-01-01T00:00:00Z,1.1,1.0,1.2,1.1,10\n", encoding="utf-8")
    try:
        load_csv(bad, instrument="EURUSD", provenance="historical")
    except DataValidationError as exc:
        print(str(exc), file=out)

    section("3-5. Señal -> validación de riesgo -> operación simulada -> cierre")
    result, run_dir = run_and_store([dataset], config, root / "runs", argv=["demo"])
    events = result.events
    closed = next((e for e in events if e["type"] == "POSITION_CLOSED"), None)
    if closed is None:
        print("La simulación no produjo ninguna operación cerrada con estos datos.", file=out)
    else:
        order_id = closed["position"]["client_order_id"]
        trail = [e for e in events if e.get("client_order_id") == order_id
                 or (e["type"] in ("POSITION_OPENED", "POSITION_CLOSED") and e["position"]["client_order_id"] == order_id)]
        signal_ts = trail[0]["bar_ts"]
        sig = next(e for e in events if e["type"] == "SIGNAL" and e["bar_ts"] == signal_ts)
        trail = [sig, *trail]
        print(format_history(trail), file=out)
        check = next(e for e in trail if e["type"] == "RISK_CHECK")
        print("\nDetalle de la validación previa:", file=out)
        for c in check["checks"]:
            print(f"  [{'OK' if c['passed'] else 'NO'}] {c['code']}: {c['detail']}", file=out)
        b = check["breakdown"]
        print(f"  Pérdida potencial en {b['account_currency']}: riesgo de precio {b['price_risk']} + "
              f"deslizamiento {b['slippage_cost']} + comisiones {b['commission_round_trip']} = {b['max_loss']}", file=out)

    section("6. Informe")
    print(format_report_summary(result.report), file=out)
    print(f"\nArchivos de la corrida: {run_dir}", file=out)
    print("  journal.jsonl (bitácora), report.md, report.json, positions.json, manifest.json", file=out)
    print(f"  Consulte: trade-agent positions \"{run_dir}\" | trade-agent history \"{run_dir}\"", file=out)
    return EXIT_OK


def cmd_shell(args, out: TextIO) -> int:
    TradeAgentShell(resolve_config(args.config), stdout=out).cmdloop()
    return EXIT_OK


# --- shell interactivo -----------------------------------------------------------------


class TradeAgentShell(cmd.Cmd):
    intro = BANNER + "\nEscriba 'help' para ver los comandos. 'stop' detiene el sistema."
    prompt = "trade-agent(sim)> "

    def __init__(self, config: AppConfig, stdin=None, stdout=None, out_root: str | Path = "runs"):
        super().__init__(stdin=stdin, stdout=stdout)
        if stdin is not None:
            self.use_rawinput = False
        self.config = config
        self.out_root = Path(out_root)
        self.datasets: dict[str, Dataset] = {}
        self.result: SimulationResult | None = None
        self.run_dir: Path | None = None

    def _print(self, text: str) -> None:
        self.stdout.write(text + "\n")

    def onecmd(self, line: str) -> bool:
        try:
            return super().onecmd(line)
        except (DataValidationError, ConfigError, RunStoreError, LiveTradingDisabledError, ValueError) as exc:
            self._print(f"ERROR: {exc}")
            return False

    def emptyline(self) -> bool:
        return False

    def default(self, line: str) -> None:
        self._print(f"Comando desconocido: {line!r}. Escriba 'help'.")

    def do_config(self, arg: str) -> None:
        """config <archivo.json> — carga una configuración (sin argumento: muestra la actual)."""
        if arg.strip():
            self.config = load_config(arg.strip())
            self._print("Configuración cargada.")
        self._print(pretty_json(self.config.to_dict()))

    def do_load(self, arg: str) -> None:
        """load <csv> [instrumento] [historical|synthetic] — carga y valida datos OHLCV."""
        parts = shlex.split(arg, posix=False)
        if not parts:
            raise ValueError("uso: load <csv> [instrumento] [historical|synthetic]")
        path = parts[0].strip('"')
        dataset = load_csv(path, instrument=parts[1] if len(parts) > 1 else None,
                           provenance=parts[2] if len(parts) > 2 else None)
        self.datasets[dataset.instrument] = dataset
        self._print("Datos válidos.\n" + format_observation(observe(dataset, self.config.strategy)))

    def do_analyze(self, arg: str) -> None:
        """analyze — resumen de mercado y recuento de señales de los datos cargados."""
        strategy = build_strategy(self.config.strategy)
        for dataset in self._require_data():
            self._print(format_observation(observe(dataset, self.config.strategy)))
            self._print(format_signal_counts(generate_signals(dataset, strategy)))

    def do_signals(self, arg: str) -> None:
        """signals [all] — lista las señales BUY/SELL (con 'all' incluye NO_TRADE)."""
        strategy = build_strategy(self.config.strategy)
        for dataset in self._require_data():
            self._print(format_signals(generate_signals(dataset, strategy), include_no_trade=arg.strip() == "all"))

    def do_simulate(self, arg: str) -> None:
        """simulate [close-at-end] [stop-after N] — ejecuta la simulación y guarda la bitácora."""
        tokens = arg.split()
        stop_after = int(tokens[tokens.index("stop-after") + 1]) if "stop-after" in tokens else None
        self.result, self.run_dir = run_and_store(
            list(self._require_data()), self.config, self.out_root,
            close_at_end=True if "close-at-end" in tokens else None, stop_after_bars=stop_after, argv=["shell", arg],
        )
        self._print(format_report_summary(self.result.report) + f"\nCorrida guardada en: {self.run_dir}")

    def do_positions(self, arg: str) -> None:
        """positions [open|closed|all] — posiciones de la última simulación."""
        result = self._require_result()
        status = arg.strip() or "all"
        if status not in ("open", "closed", "all"):
            raise ValueError("uso: positions [open|closed|all]")
        self._print(format_positions([to_jsonable(p.to_dict()) for p in result.portfolio.all_positions()], status))

    def do_history(self, arg: str) -> None:
        """history [TIPO ...] — eventos de la bitácora (p. ej. history ORDER_FILLED POSITION_CLOSED)."""
        result = self._require_result()
        types = {t.upper() for t in arg.split()} or None
        self._print(format_history(result.events, types))

    def do_report(self, arg: str) -> None:
        """report — resumen del informe de la última simulación."""
        self._print(format_report_summary(self._require_result().report))

    def do_stop(self, arg: str) -> bool:
        """stop — detiene el sistema (no hay procesos ni órdenes en segundo plano)."""
        self._print("Sistema detenido. No quedan órdenes pendientes ni procesos activos.")
        return True

    do_exit = do_quit = do_stop

    def do_EOF(self, arg: str) -> bool:  # noqa: N802 - nombre exigido por cmd
        return self.do_stop(arg)

    def _require_data(self):
        if not self.datasets:
            raise ValueError("no hay datos cargados; use 'load <csv>'.")
        return [self.datasets[k] for k in sorted(self.datasets)]

    def _require_result(self) -> SimulationResult:
        if self.result is None:
            raise ValueError("todavía no hay simulación; use 'simulate'.")
        return self.result


# --- parser y punto de entrada ---------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trade-agent", description=BANNER)
    parser.add_argument("--version", action="version", version=f"trade-agent {__version__} (simulación)")
    sub = parser.add_subparsers(dest="command", required=True)

    def data_args(p, multiple=False):
        p.add_argument("csv", nargs="+" if multiple else 1, help="archivo(s) CSV OHLCV")
        p.add_argument("--instrument", help="símbolo (si no viene en el .meta.json)")
        p.add_argument("--provenance", choices=["historical", "synthetic"], help="procedencia de los datos")
        p.add_argument("--config", help="configuración JSON (por defecto: la integrada, igual a examples/config.json)")

    p = sub.add_parser("load", help="cargar y validar datos; resumen de mercado")
    data_args(p)
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("analyze", help="analizar datos y revisar señales")
    data_args(p, multiple=True)
    p.add_argument("--all", action="store_true", help="incluir señales NO_TRADE")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("simulate", help="simular operaciones y guardar la bitácora")
    data_args(p, multiple=True)
    p.add_argument("--out", default="runs", help="directorio raíz de corridas (por defecto: runs)")
    p.add_argument("--close-at-end", action="store_true", help="cerrar posiciones abiertas al final de los datos")
    p.add_argument("--stop-after-bars", type=int, help="detener el sistema tras N barras (simula una parada)")
    p.set_defaults(func=cmd_simulate)

    for name, func, help_text in (("positions", cmd_positions, "consultar posiciones de una corrida"),
                                  ("history", cmd_history, "examinar la bitácora de una corrida"),
                                  ("report", cmd_report, "ver el informe de una corrida")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("run_dir")
        if name == "positions":
            p.add_argument("--status", choices=["open", "closed", "all"], default="all")
        if name == "history":
            p.add_argument("--type", nargs="*", help="filtrar por tipo de evento")
            p.add_argument("--limit", type=int, help="mostrar solo los últimos N eventos")
        if name == "report":
            p.add_argument("--markdown", action="store_true")
        p.set_defaults(func=func)

    p = sub.add_parser("generate-synthetic", help="generar datos SINTÉTICOS deterministas")
    p.add_argument("output")
    p.add_argument("--instrument", default="EURUSD")
    p.add_argument("--bars", type=int, default=600)
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("demo", help="demostración del ciclo completo con datos sintéticos")
    p.add_argument("--out", help="directorio de salida (por defecto: uno temporal)")
    p.add_argument("--config")
    p.add_argument("--bars", type=int, default=600)
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("shell", help="shell interactivo (comando 'stop' para detener)")
    p.add_argument("--config")
    p.set_defaults(func=cmd_shell)
    return parser


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        isatty = getattr(stream, "isatty", lambda: True)()
        if not isatty and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_streams()
    args = build_parser().parse_args(argv)
    out = sys.stdout
    if args.command != "shell":
        print(BANNER, file=out)
    try:
        return args.func(args, out)
    except (DataValidationError, ConfigError, RunStoreError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INVALID
    except LiveTradingDisabledError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_LIVE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
