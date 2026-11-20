from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import BASE_CONFIG, make_config
from trade_agent.config import DEFAULT_CONFIG, ConfigError, default_config, load_config

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_example_config_matches_builtin_default():
    assert load_config(EXAMPLES / "config.json") == default_config()
    assert json.loads(json.dumps(DEFAULT_CONFIG)) == json.loads(json.dumps(BASE_CONFIG))


def test_decimal_values_are_exact_even_from_json_floats(tmp_path):
    path = tmp_path / "c.json"
    data = json.loads(json.dumps(BASE_CONFIG))
    data["instruments"]["EURUSD"]["margin_rate"] = 0.0333
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_config(path).instruments["EURUSD"].margin_rate == Decimal("0.0333")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["risk"].pop("daily_loss_limit"), "daily_loss_limit"),
        (lambda d: d["risk"].update(max_los_per_trade="5"), "desconocida"),
        (lambda d: d["risk"].update(max_loss_per_trade="-1"), "mayor que cero"),
        (lambda d: d["risk"].update(max_open_positions=0), ">= 1"),
        (lambda d: d["strategy"].update(fast_period=40), "fast_period"),
        (lambda d: d["account"].update(currency="pesos"), "divisa"),
        (lambda d: d["instruments"]["EURUSD"].update(margin_rate="2"), "margin_rate"),
        (lambda d: d.update(mode="LIVE"), "deshabilitado"),
        (lambda d: d["instruments"]["EURUSD"].update(investor_password="x"), "credenciales"),
    ],
)
def test_invalid_configuration_is_rejected_with_reason(mutate, message):
    with pytest.raises(ConfigError, match=message):
        make_config(mutate)


def test_incomplete_instrument_spec_is_allowed_but_reported():
    config = make_config(lambda d: d["instruments"]["EURUSD"].pop("spread"))
    assert config.instruments["EURUSD"].missing_fields("MXN") == ["spread"]
    other = make_config(lambda d: d["instruments"]["EURUSD"].pop("quote_to_account_rate"))
    assert other.instruments["EURUSD"].missing_fields("MXN") == ["quote_to_account_rate"]
    assert other.instruments["EURUSD"].missing_fields("USD") == []
