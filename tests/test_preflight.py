"""Tests for preflight safety checks (task 0.5.15)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymm.config import (
    BotConfig,
    Config,
    Credentials,
    ExchangeConfig,
    RiskConfig,
)
from polymm.preflight import Severity, preflight

pytestmark = pytest.mark.unit


def base_config(**overrides) -> Config:
    exchange = ExchangeConfig(
        chain_id=137,
        ctf_exchange_address="0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        neg_risk_exchange_address="0xC5d563A36AE78145C45a50134d48A1215220f80a",
    )
    kwargs: dict = {
        "bot": BotConfig(enable_trading=True, mock_trading=False),
        "exchange": exchange,
        "risk": RiskConfig(),
        "credentials": Credentials(private_key="0x" + "ab" * 32),
    }
    kwargs.update(overrides)
    return Config(**kwargs)


def codes(report) -> set[str]:
    return {f.code for f in report.findings}


def test_fully_configured_live_passes() -> None:
    report = preflight(base_config())
    assert report.ok, report.findings
    # L2 creds missing is only a warning.
    assert "no_l2" in codes(report)


def test_trading_disabled_is_error() -> None:
    report = preflight(base_config(bot=BotConfig(enable_trading=False, mock_trading=False)))
    assert not report.ok
    assert "trading_disabled" in codes(report)


def test_mock_enabled_is_error() -> None:
    report = preflight(base_config(bot=BotConfig(enable_trading=True, mock_trading=True)))
    assert not report.ok
    assert "mock_enabled" in codes(report)


def test_no_signer_is_error() -> None:
    report = preflight(base_config(credentials=Credentials()))
    assert not report.ok
    assert "no_signer" in codes(report)


def test_unknown_chain_is_error() -> None:
    exchange = ExchangeConfig(
        chain_id=999999,
        ctf_exchange_address="0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        neg_risk_exchange_address="0xC5d563A36AE78145C45a50134d48A1215220f80a",
    )
    report = preflight(base_config(exchange=exchange))
    assert not report.ok
    assert "unknown_chain" in codes(report)


def test_order_cap_above_total_is_error() -> None:
    risk = RiskConfig(max_order_bankroll_fraction=0.6, max_total_bankroll_fraction=0.5)
    report = preflight(base_config(risk=risk))
    assert not report.ok
    assert "cap_order_gt_total" in codes(report)


def test_below_min_bankroll_is_error() -> None:
    report = preflight(base_config(), bankroll_usd=Decimal("10"))
    assert not report.ok
    assert "below_min_bankroll" in codes(report)


def test_tiny_bankroll_warns() -> None:
    report = preflight(base_config(), bankroll_usd=Decimal("80"))
    assert report.ok
    assert "tiny_bankroll" in codes(report)


def test_zero_loss_limit_warns() -> None:
    report = preflight(base_config(risk=RiskConfig(daily_loss_limit_usd=0)))
    assert "no_loss_limit" in codes(report)


def test_weak_burst_guard_warns() -> None:
    report = preflight(base_config(risk=RiskConfig(consecutive_trigger=1)))
    assert "weak_burst_guard" in codes(report)


def test_report_separates_errors_and_warnings() -> None:
    report = preflight(base_config(credentials=Credentials()), bankroll_usd=Decimal("80"))
    assert any(f.severity is Severity.ERROR for f in report.errors)
    assert all(f.severity is Severity.ERROR for f in report.errors)
    assert all(f.severity is Severity.WARNING for f in report.warnings)
