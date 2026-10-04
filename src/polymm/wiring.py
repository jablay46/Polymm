"""Translate a validated :class:`~polymm.config.Config` into runtime objects.

This module is the single seam between configuration and the strategy/risk
engines. Before it existed, ``strategy.*`` in YAML was parsed and then never
read — the bot always ran with hard-coded defaults. Keeping the mapping here
(one place, covered by tests) is what makes the config actually authoritative.
"""

from __future__ import annotations

from decimal import Decimal

from polymm.config import Config
from polymm.risk import RiskLimits
from polymm.strategy import ArbConfig


def _dec(value: float) -> Decimal:
    return Decimal(str(value))


def arb_config_from(config: Config) -> ArbConfig:
    """Build an :class:`ArbConfig` from the strategy section."""
    s = config.strategy
    return ArbConfig(
        min_edge=_dec(s.complete_set_min_edge),
        max_basket_cost=_dec(s.max_basket_cost),
        max_leg_price=_dec(s.max_leg_price),
        max_spread=_dec(s.max_spread),
        fee_rate=_dec(s.fee_rate),
        target_size=_dec(s.target_size),
        min_visible_size=_dec(s.min_visible_size),
    )


def risk_limits_from(config: Config) -> RiskLimits:
    """Build :class:`RiskLimits` from the risk section."""
    return RiskLimits.from_config(config.risk)
