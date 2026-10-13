"""Sicherheitslimits sind unveraenderlich; nur der PAPER-Modus ist moeglich."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from trading.config import ConfigError, Settings, TradingMode, load_settings
from trading.execution.paper_executor import PaperExecutor
from trading.safety.limits import (
    DEFAULT_LIMITS,
    HARD_MAX_PORTFOLIO_RISK,
    LimitViolationError,
    SafetyLimits,
)

D = Decimal


def test_default_portfolio_risk_cap_is_two_percent():
    assert DEFAULT_LIMITS.max_portfolio_risk == D("0.02")
    assert HARD_MAX_PORTFOLIO_RISK == D("0.02")


def test_limits_cannot_be_changed_at_runtime():
    with pytest.raises(FrozenInstanceError):
        DEFAULT_LIMITS.max_portfolio_risk = D("0.5")  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_portfolio_risk": D("0.05")},
        {"max_risk_per_trade": D("0.02")},
        {"max_leverage": D("10")},
        {"max_open_positions": 50},
        {"max_daily_loss": D("0.5")},
        {"max_drawdown": D("0.9")},
        {"max_data_age_s": 600},
        {"max_gross_exposure": D("1.5")},
        {"max_stress_loss": D("0.20")},
        {"stress_shock": D("0.05")},
        {"stress_shock": D("1")},
        {"max_mark_age_s": 99999},
        {"max_spread_bps": D("500")},
        {"liquidation_buffer": D("1.0")},
        {"maintenance_margin_rate": D("0.001")},
        {"max_leverage": D("0")},
        {"max_portfolio_risk": D("-0.01")},
    ],
)
def test_limits_cannot_be_loosened(overrides):
    with pytest.raises(LimitViolationError):
        DEFAULT_LIMITS.tightened(**overrides)


def test_exposure_and_stress_caps_have_the_agreed_values():
    assert DEFAULT_LIMITS.max_gross_exposure == D("1.00")  # 100 % Brutto-Exposure
    assert DEFAULT_LIMITS.stress_shock == D("0.10")  # 10 % Kursschock ...
    assert DEFAULT_LIMITS.max_stress_loss == D("0.05")  # ... darf hoechstens 5 % der Equity kosten
    stricter = DEFAULT_LIMITS.tightened(max_gross_exposure=D("0.5"), stress_shock=D("0.25"),
                                        max_stress_loss=D("0.02"))
    assert (stricter.max_gross_exposure, stricter.stress_shock, stricter.max_stress_loss) == \
        (D("0.5"), D("0.25"), D("0.02"))


def test_limits_can_be_tightened():
    strict = DEFAULT_LIMITS.tightened(max_leverage=D("2"), max_portfolio_risk=D("0.01"))
    assert strict.max_leverage == D("2")
    assert strict.max_portfolio_risk == D("0.01")
    assert DEFAULT_LIMITS.max_leverage == D("5")  # Original unveraendert


def test_trade_risk_cannot_exceed_portfolio_risk():
    with pytest.raises(LimitViolationError):
        SafetyLimits(max_portfolio_risk=D("0.004"))  # kleiner als Risiko je Trade (0,005)


# ----------------------------------------------------------------- Konfiguration


def test_trading_mode_has_only_paper():
    assert [m.value for m in TradingMode] == ["paper"]
    assert PaperExecutor.MODE is TradingMode.PAPER


def test_default_settings_are_paper():
    assert load_settings({}).mode is TradingMode.PAPER


@pytest.mark.parametrize("value", ["live", "LIVE", "real", "", "testnet"])
def test_non_paper_mode_is_refused(value):
    with pytest.raises(ConfigError):
        load_settings({"TRADING_MODE": value})


@pytest.mark.parametrize(
    "name", ["COINBASE_API_KEY", "COINBASE_API_SECRET", "BITGET_API_KEY", "BITGET_PASSPHRASE"]
)
def test_exchange_credentials_in_environment_block_startup(name):
    with pytest.raises(ConfigError):
        load_settings({"TRADING_MODE": "paper", name: "irgendwas"})


def test_unrelated_environment_is_fine():
    assert load_settings({"PATH": "/usr/bin", "HOME": "/x"}).mode is TradingMode.PAPER


def test_database_password_never_appears_in_repr():
    s = load_settings({"DATABASE_URL": "postgresql://u:geheim123@db/x"})
    assert "geheim123" not in repr(s)
    assert isinstance(s, Settings)
