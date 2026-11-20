"""Configuración validada: cuenta simulada, límites de riesgo, estrategia e instrumentos.

Reglas de seguridad aplicadas al cargar:

* ``mode`` solo admite ``"simulation"``; cualquier otro valor (p. ej. ``"live"``) se rechaza.
* Cualquier campo con aspecto de credencial (password, login, server, token...) se rechaza.
* Las claves desconocidas se rechazan: una errata en un límite de riesgo no debe ignorarse.

Las especificaciones de instrumento pueden estar incompletas: el motor de riesgo rechazará
entonces cada operación indicando qué dato de coste o exposición falta.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SIMULATION_MODE = "simulation"

FORBIDDEN_KEYS = {
    "password", "passwd", "pass", "login", "server", "token", "api_key", "apikey", "secret",
    "account_number", "account_id", "credentials", "investor_password", "broker_token",
}


class ConfigError(ValueError):
    pass


def _dec(value: Any, where: str) -> Decimal:
    if isinstance(value, bool):
        raise ConfigError(f"{where}: se esperaba un número, se recibió {value!r}.")
    try:
        result = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ConfigError(f"{where}: '{value}' no es un número válido.") from None
    if not result.is_finite():
        raise ConfigError(f"{where}: el valor debe ser finito.")
    return result


def _positive(value: Any, where: str) -> Decimal:
    result = _dec(value, where)
    if result <= 0:
        raise ConfigError(f"{where}: debe ser mayor que cero (recibido {result}).")
    return result


def _non_negative(value: Any, where: str) -> Decimal:
    result = _dec(value, where)
    if result < 0:
        raise ConfigError(f"{where}: no puede ser negativo (recibido {result}).")
    return result


def _int(value: Any, where: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: se esperaba un entero, se recibió {value!r}.")
    if value < minimum:
        raise ConfigError(f"{where}: debe ser >= {minimum} (recibido {value}).")
    return value


def _section(data: dict, name: str, required: bool = True) -> dict:
    value = data.get(name)
    if value is None:
        if required:
            raise ConfigError(f"Falta la sección obligatoria '{name}'.")
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"La sección '{name}' debe ser un objeto.")
    return value


def _reject_unknown(section: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ConfigError(f"{where}: clave(s) desconocida(s) {unknown}. Permitidas: {sorted(allowed)}.")


def _reject_credentials(node: Any, path: str = "") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).strip().lower() in FORBIDDEN_KEYS:
                raise ConfigError(
                    f"La configuración contiene el campo '{path}{key}': Trade Agent 0.1 no acepta "
                    "credenciales de bróker ni datos de acceso a cuentas reales."
                )
            _reject_credentials(value, f"{path}{key}.")
    elif isinstance(node, list):
        for item in node:
            _reject_credentials(item, path)


def _currency(value: Any, where: str) -> str:
    text = str(value or "").strip().upper()
    if len(text) != 3 or not text.isalpha():
        raise ConfigError(f"{where}: código de divisa inválido {value!r} (ej. 'MXN', 'USD').")
    return text


@dataclass(frozen=True)
class AccountConfig:
    currency: str
    initial_capital: Decimal


@dataclass(frozen=True)
class RiskLimits:
    max_budget_per_trade: Decimal
    max_loss_per_trade: Decimal
    profit_target_per_trade: Decimal
    daily_loss_limit: Decimal
    max_open_positions: int


@dataclass(frozen=True)
class StrategyParams:
    name: str = "sma_cross_rsi"
    fast_period: int = 10
    slow_period: int = 30
    rsi_period: int = 14
    rsi_buy_max: Decimal = Decimal(70)
    rsi_sell_min: Decimal = Decimal(30)
    atr_period: int = 14
    stop_atr_multiple: Decimal = Decimal(2)
    lookback_bars: int = 200

    @property
    def warmup_bars(self) -> int:
        return max(self.slow_period + 1, self.rsi_period + 1, self.atr_period + 1)


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    quote_currency: str | None = None
    quote_to_account_rate: Decimal | None = None
    contract_size: Decimal | None = None
    tick_size: Decimal | None = None
    spread: Decimal | None = None
    commission_per_lot_per_side: Decimal | None = None
    slippage: Decimal = Decimal(0)
    min_lot: Decimal | None = None
    lot_step: Decimal | None = None
    max_lot: Decimal | None = None
    margin_rate: Decimal | None = None

    REQUIRED = (
        "quote_currency", "contract_size", "tick_size", "spread",
        "commission_per_lot_per_side", "min_lot", "lot_step", "max_lot", "margin_rate",
    )

    def missing_fields(self, account_currency: str) -> list[str]:
        missing = [name for name in self.REQUIRED if getattr(self, name) is None]
        if self.quote_currency and self.quote_currency != account_currency and self.quote_to_account_rate is None:
            missing.append("quote_to_account_rate")
        return missing

    def fx_rate(self, account_currency: str) -> Decimal:
        """Factor para convertir importes en divisa de cotización a divisa de la cuenta."""
        if self.quote_currency == account_currency:
            return Decimal(1)
        if self.quote_to_account_rate is None:
            raise ConfigError(f"{self.symbol}: falta quote_to_account_rate.")
        return self.quote_to_account_rate

    @property
    def half_spread(self) -> Decimal:
        return (self.spread or Decimal(0)) / 2


@dataclass(frozen=True)
class SimulationSettings:
    close_open_positions_at_end: bool = False


@dataclass(frozen=True)
class AppConfig:
    mode: str
    account: AccountConfig
    risk: RiskLimits
    strategy: StrategyParams
    instruments: dict[str, InstrumentSpec]
    simulation: SimulationSettings

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        if not isinstance(data, dict):
            raise ConfigError("La configuración debe ser un objeto JSON.")
        _reject_credentials(data)
        _reject_unknown(data, {"mode", "account", "risk", "strategy", "instruments", "simulation"}, "raíz")
        mode = str(data.get("mode", SIMULATION_MODE)).strip().lower()
        if mode != SIMULATION_MODE:
            raise ConfigError(
                f"El modo '{mode}' está deshabilitado: Trade Agent 0.1 solo existe en modo "
                "'simulation' (no hay ejecución con dinero real)."
            )

        account_d = _section(data, "account")
        _reject_unknown(account_d, {"currency", "initial_capital"}, "account")
        account = AccountConfig(
            currency=_currency(account_d.get("currency"), "account.currency"),
            initial_capital=_positive(account_d.get("initial_capital"), "account.initial_capital"),
        )

        risk_d = _section(data, "risk")
        risk_keys = {f.name for f in fields(RiskLimits)}
        _reject_unknown(risk_d, risk_keys, "risk")
        for key in risk_keys:
            if key not in risk_d:
                raise ConfigError(f"risk: falta el límite obligatorio '{key}'.")
        risk = RiskLimits(
            max_budget_per_trade=_positive(risk_d["max_budget_per_trade"], "risk.max_budget_per_trade"),
            max_loss_per_trade=_positive(risk_d["max_loss_per_trade"], "risk.max_loss_per_trade"),
            profit_target_per_trade=_positive(risk_d["profit_target_per_trade"], "risk.profit_target_per_trade"),
            daily_loss_limit=_positive(risk_d["daily_loss_limit"], "risk.daily_loss_limit"),
            max_open_positions=_int(risk_d["max_open_positions"], "risk.max_open_positions"),
        )

        strategy = _parse_strategy(_section(data, "strategy", required=False))

        instruments_d = _section(data, "instruments", required=False)
        instruments = {
            str(symbol).upper(): _parse_instrument(str(symbol).upper(), spec)
            for symbol, spec in sorted(instruments_d.items())
        }

        sim_d = _section(data, "simulation", required=False)
        _reject_unknown(sim_d, {"close_open_positions_at_end"}, "simulation")
        close_at_end = sim_d.get("close_open_positions_at_end", False)
        if not isinstance(close_at_end, bool):
            raise ConfigError("simulation.close_open_positions_at_end debe ser true o false.")

        return cls(SIMULATION_MODE, account, risk, strategy, instruments, SimulationSettings(close_at_end))

    def to_dict(self) -> dict:
        """Representación canónica (Decimal como texto), usada para el run_id y el manifiesto."""

        def plain(obj: Any) -> Any:
            if isinstance(obj, Decimal):
                return str(obj)
            return obj

        return {
            "mode": self.mode,
            "account": {k: plain(v) for k, v in vars(self.account).items()},
            "risk": {k: plain(v) for k, v in vars(self.risk).items()},
            "strategy": {k: plain(v) for k, v in vars(self.strategy).items()},
            "instruments": {
                symbol: {f.name: plain(getattr(spec, f.name)) for f in fields(spec) if f.name != "symbol"}
                for symbol, spec in self.instruments.items()
            },
            "simulation": dict(vars(self.simulation)),
        }


def _parse_strategy(d: dict) -> StrategyParams:
    allowed = {f.name for f in fields(StrategyParams)}
    _reject_unknown(d, allowed, "strategy")
    defaults = StrategyParams()
    name = str(d.get("name", defaults.name))
    if name != "sma_cross_rsi":
        raise ConfigError(f"strategy.name: estrategia desconocida '{name}' (disponible: 'sma_cross_rsi').")
    params = StrategyParams(
        name=name,
        fast_period=_int(d.get("fast_period", defaults.fast_period), "strategy.fast_period", 2),
        slow_period=_int(d.get("slow_period", defaults.slow_period), "strategy.slow_period", 3),
        rsi_period=_int(d.get("rsi_period", defaults.rsi_period), "strategy.rsi_period", 2),
        rsi_buy_max=_dec(d.get("rsi_buy_max", defaults.rsi_buy_max), "strategy.rsi_buy_max"),
        rsi_sell_min=_dec(d.get("rsi_sell_min", defaults.rsi_sell_min), "strategy.rsi_sell_min"),
        atr_period=_int(d.get("atr_period", defaults.atr_period), "strategy.atr_period", 2),
        stop_atr_multiple=_positive(d.get("stop_atr_multiple", defaults.stop_atr_multiple), "strategy.stop_atr_multiple"),
        lookback_bars=_int(d.get("lookback_bars", defaults.lookback_bars), "strategy.lookback_bars", 2),
    )
    if params.fast_period >= params.slow_period:
        raise ConfigError("strategy: fast_period debe ser menor que slow_period.")
    if not (0 < params.rsi_sell_min < params.rsi_buy_max < 100):
        raise ConfigError("strategy: se requiere 0 < rsi_sell_min < rsi_buy_max < 100.")
    if params.lookback_bars < params.warmup_bars:
        raise ConfigError(f"strategy.lookback_bars debe ser >= {params.warmup_bars} (calentamiento).")
    return params


def _parse_instrument(symbol: str, d: Any) -> InstrumentSpec:
    if not isinstance(d, dict):
        raise ConfigError(f"instruments.{symbol} debe ser un objeto.")
    allowed = {f.name for f in fields(InstrumentSpec)} - {"symbol"}
    _reject_unknown(d, allowed, f"instruments.{symbol}")
    where = f"instruments.{symbol}"

    def opt(name: str, parser) -> Decimal | None:
        return None if d.get(name) is None else parser(d[name], f"{where}.{name}")

    margin_rate = opt("margin_rate", _positive)
    if margin_rate is not None and margin_rate > 1:
        raise ConfigError(f"{where}.margin_rate debe estar entre 0 y 1 (ej. 0.0333 para 1:30).")
    return InstrumentSpec(
        symbol=symbol,
        quote_currency=_currency(d["quote_currency"], f"{where}.quote_currency") if d.get("quote_currency") else None,
        quote_to_account_rate=opt("quote_to_account_rate", _positive),
        contract_size=opt("contract_size", _positive),
        tick_size=opt("tick_size", _positive),
        spread=opt("spread", _non_negative),
        commission_per_lot_per_side=opt("commission_per_lot_per_side", _non_negative),
        slippage=_non_negative(d.get("slippage", 0), f"{where}.slippage"),
        min_lot=opt("min_lot", _positive),
        lot_step=opt("lot_step", _positive),
        max_lot=opt("max_lot", _positive),
        margin_rate=margin_rate,
    )


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except FileNotFoundError:
        raise ConfigError(f"No existe el archivo de configuración {path}.") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name} no es JSON válido: {exc}") from None
    return AppConfig.from_dict(data)


DEFAULT_CONFIG: dict = {
    "mode": "simulation",
    "account": {"currency": "MXN", "initial_capital": "100000"},
    "risk": {
        "max_budget_per_trade": "25000",
        "max_loss_per_trade": "600",
        "profit_target_per_trade": "1200",
        "daily_loss_limit": "1500",
        "max_open_positions": 2,
    },
    "strategy": {
        "name": "sma_cross_rsi",
        "fast_period": 10,
        "slow_period": 30,
        "rsi_period": 14,
        "rsi_buy_max": "70",
        "rsi_sell_min": "30",
        "atr_period": 14,
        "stop_atr_multiple": "2",
        "lookback_bars": 200,
    },
    "instruments": {
        "EURUSD": {
            "quote_currency": "USD",
            "quote_to_account_rate": "17.00",
            "contract_size": "100000",
            "tick_size": "0.00001",
            "spread": "0.00012",
            "commission_per_lot_per_side": "60",
            "slippage": "0",
            "min_lot": "0.01",
            "lot_step": "0.01",
            "max_lot": "5",
            "margin_rate": "0.0333",
        }
    },
    "simulation": {"close_open_positions_at_end": False},
}


def default_config() -> AppConfig:
    return AppConfig.from_dict(json.loads(json.dumps(DEFAULT_CONFIG)))
