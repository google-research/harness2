from __future__ import annotations

import io
import json

from trade_agent import cli
from trade_agent.config import default_config


def run_cli(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_simulate_then_query_positions_history_and_report(tmp_path, capsys, synthetic_csv):
    code, out, _ = run_cli(capsys, "simulate", str(synthetic_csv), "--out", str(tmp_path))
    assert code == 0 and "Corrida guardada en:" in out
    [run_dir] = [p for p in tmp_path.iterdir() if p.is_dir()]
    meta = json.loads((run_dir / "run_meta.json").read_text("utf-8"))
    assert meta["duration_seconds"] >= 0

    code, out, _ = run_cli(capsys, "positions", str(run_dir), "--status", "closed")
    assert code == 0 and "CLOSED" in out and "OPEN " not in out
    code, out, _ = run_cli(capsys, "history", str(run_dir), "--type", "ORDER_FILLED", "POSITION_CLOSED")
    assert code == 0 and "EJECUTADA (SIMULADA)" in out and "cerrada por" in out
    code, out, _ = run_cli(capsys, "report", str(run_dir), "--markdown")
    assert code == 0 and "# Informe de simulación" in out


def test_simulate_stop_after_bars_reports_stopped(tmp_path, capsys, synthetic_csv):
    code, out, _ = run_cli(capsys, "simulate", str(synthetic_csv), "--out", str(tmp_path), "--stop-after-bars", "50")
    assert code == 0 and "STOPPED" in out


def test_analyze_lists_signals_with_justification(capsys, synthetic_csv):
    code, out, _ = run_cli(capsys, "analyze", str(synthetic_csv))
    assert code == 0
    assert "SINTÉTICOS" in out and "NO es una cotización actual" in out
    assert "BUY" in out and "cruzó" in out


def test_live_configuration_is_refused_by_the_cli(tmp_path, capsys, synthetic_csv, base_config_dict):
    path = tmp_path / "live.json"
    path.write_text(json.dumps({**base_config_dict, "mode": "live"}), encoding="utf-8")
    code, _, err = run_cli(capsys, "simulate", str(synthetic_csv), "--config", str(path), "--out", str(tmp_path))
    assert code == 2 and "deshabilitado" in err


def test_generate_synthetic_writes_csv_and_provenance(tmp_path, capsys):
    code, out, _ = run_cli(capsys, "generate-synthetic", str(tmp_path / "x.csv"), "--bars", "60", "--seed", "3")
    assert code == 0
    assert json.loads((tmp_path / "x.meta.json").read_text("utf-8"))["provenance"] == "SYNTHETIC"


def test_interactive_shell_full_session(tmp_path, synthetic_csv):
    script = "\n".join([
        "positions",                  # error comprensible: aún no hay simulación
        f'load "{synthetic_csv}"',
        "analyze",
        "signals",
        "simulate",
        "positions closed",
        "history ORDER_FILLED",
        "report",
        "stop",
        "report",                     # no debe ejecutarse: el sistema ya se detuvo
    ]) + "\n"
    output = io.StringIO()
    shell = cli.TradeAgentShell(default_config(), stdin=io.StringIO(script), stdout=output, out_root=tmp_path)
    shell.cmdloop()
    text = output.getvalue()
    assert "ERROR: todavía no hay simulación" in text
    assert "Datos válidos." in text and "Señales: BUY=" in text
    assert "Corrida guardada en:" in text and "CLOSED" in text and "EJECUTADA (SIMULADA)" in text
    assert text.rstrip().endswith("Sistema detenido. No quedan órdenes pendientes ni procesos activos.")
