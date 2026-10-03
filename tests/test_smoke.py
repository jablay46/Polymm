"""Smoke test: the test harness itself is wired up (task 0.5.1)."""

from __future__ import annotations

import pytest

import polymm
from polymm.config import load_config

pytestmark = pytest.mark.unit


def test_package_imports() -> None:
    assert polymm.__version__ == "0.1.0"


def test_synthetic_book_fixture(two_sided_book) -> None:
    """Fixtures build exact, network-free scenarios."""
    assert two_sided_book.best_bid == 0.49
    assert two_sided_book.best_ask == 0.51
    assert two_sided_book.spread == pytest.approx(0.02)
    assert two_sided_book.mid == pytest.approx(0.50)


def test_depth_guard_fixture(thin_book) -> None:
    assert thin_book.depth_usd("BUY", 5) == pytest.approx(0.51)


def test_example_config_parses() -> None:
    """The committed example config must load cleanly (dry-run defaults)."""
    cfg = load_config("config.example.yaml", env={})
    assert cfg.bot.live_trading_allowed is False
    assert cfg.exchange.chain_id == 137
    assert cfg.risk.max_order_bankroll_fraction == pytest.approx(0.05)
