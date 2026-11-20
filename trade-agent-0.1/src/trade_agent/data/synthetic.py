"""Generador determinista de barras OHLCV SINTÉTICAS (paseo aleatorio por regímenes).

Solo se usa ``random.Random(seed).random()``, cuya secuencia Python garantiza estable entre
versiones; los precios se construyen con ``Decimal``. Las fechas empiezan en 2020 a propósito
para que nunca se confundan con cotizaciones actuales, y el archivo ``.meta.json`` que
acompaña al CSV declara la procedencia SINTÉTICA.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trade_agent.data.ingest import REQUIRED_COLUMNS, Provenance, meta_path_for
from trade_agent.domain import Bar

GENERATOR_NAME = "trade_agent.synthetic.regime_random_walk"
GENERATOR_VERSION = 1
DEFAULT_START = datetime(2020, 1, 6, tzinfo=UTC)


def _dec(value: float, decimals: int) -> Decimal:
    return Decimal(f"{value:.{decimals}f}")


def generate_bars(
    n_bars: int,
    seed: int,
    start: datetime = DEFAULT_START,
    step: timedelta = timedelta(hours=1),
    start_price: Decimal = Decimal("1.10000"),
    volatility: float = 0.0008,
    decimals: int = 5,
) -> list[Bar]:
    if n_bars <= 0:
        raise ValueError("n_bars debe ser positivo")
    rng = random.Random(seed)
    bars: list[Bar] = []
    close = start_price
    regime_left = 0
    drift = 0.0
    floor = Decimal(1).scaleb(-decimals)
    for index in range(n_bars):
        if regime_left == 0:
            regime_left = 40 + int(rng.random() * 81)
            drift = (int(rng.random() * 3) - 1) * volatility * 0.35
        regime_left -= 1
        open_ = close
        close = max(open_ + _dec(drift + (2 * rng.random() - 1) * volatility, decimals), floor)
        high = max(open_, close) + _dec(rng.random() * volatility * 0.6, decimals)
        low = max(min(open_, close) - _dec(rng.random() * volatility * 0.6, decimals), floor)
        volume = Decimal(200 + int(rng.random() * 1800))
        bars.append(Bar(start + step * index, open_, high, low, close, volume))
    return bars


def write_synthetic_csv(
    path: str | Path,
    instrument: str = "EURUSD",
    n_bars: int = 600,
    seed: int = 7,
    timeframe: str = "H1",
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    bars = generate_bars(n_bars=n_bars, seed=seed)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(REQUIRED_COLUMNS)
        for bar in bars:
            writer.writerow(
                [
                    bar.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                ]
            )
    meta = {
        "instrument": instrument.upper(),
        "provenance": Provenance.SYNTHETIC.value,
        "timeframe": timeframe,
        "generator": {
            "name": GENERATOR_NAME,
            "version": GENERATOR_VERSION,
            "seed": seed,
            "n_bars": n_bars,
        },
        "warning": Provenance.SYNTHETIC.notice,
    }
    meta_path_for(path).write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
