"""Tests for config→runtime wiring (audit fix S2).

Before this module existed, the ``strategy`` section of the YAML was parsed
and then never read: the bot always ran on hard-coded defaults.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymm.config import (
    BotConfig,
    Config,
    Credentials,
    ExchangeConfig,
    RiskConfig,
    StrategyConfig,
)
from polymm.wiring import arb_config_from, risk_limits_from

pytestmark = pytest.mark.unit


def base_config(**overrides) -> Config:
    kwargs: dict = {
        "bot": BotConfig(),
        "exchange": ExchangeConfig(
            chain_id=137,
            ctf_exchange_address="0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
            neg_risk_exchange_address="0xC5d563A36AE78145C45a50134d48A1215220f80a",
        ),
        "risk": RiskConfig(),
        "strategy": StrategyConfig(),
        "credentials": Credentials(),
    }
    kwargs.update(overrides)
    return Config(**kwargs)


def test_strategy_section_reaches_arb_config() -> None:
    cfg = base_config(
        strategy=StrategyConfig(
            complete_set_min_edge=0.03,
            max_basket_cost=0.97,
            fee_rate=0.01,
            target_size=25.0,
            max_spread=0.05,
        )
    )
    arb = arb_config_from(cfg)
    assert arb.min_edge == Decimal("0.03")
    assert arb.max_basket_cost == Decimal("0.97")
    assert arb.fee_rate == Decimal("0.01")
    assert arb.target_size == Decimal("25.0")
    assert arb.max_spread == Decimal("0.05")


def test_risk_section_reaches_risk_limits() -> None:
    cfg = base_config(risk=RiskConfig(max_order_bankroll_fraction=0.1, min_depth_usd=50.0))
    limits = risk_limits_from(cfg)
    assert limits.max_order_notional == Decimal("0.1")
    assert limits.min_depth_usd == Decimal("50.0")
