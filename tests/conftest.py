"""Shared pytest fixtures.

Tier 1 tests (unit) must run with no network and no credentials. Any
fixture that would need a wallet is defined so it *skips* cleanly instead
of failing, which keeps CI green for contributors without secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

# ── Synthetic order-book primitives ───────────────────────────
# Real order books are lists of (price, size) levels sorted best-first.
# These tiny helpers let unit tests build exact scenarios without a feed.


@dataclass
class Level:
    price: float
    size: float


@dataclass
class Book:
    """A minimal two-sided book: bids descending, asks ascending."""

    bids: list[Level] = field(default_factory=list)
    asks: list[Level] = field(default_factory=list)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def depth_usd(self, side: str, levels: int) -> float:
        """USD resting on the side a taker would consume."""
        book = self.asks if side.upper() == "BUY" else self.bids
        return sum(lv.price * lv.size for lv in book[:levels])


@pytest.fixture
def two_sided_book() -> Book:
    """A tight, liquid book around 0.50."""
    return Book(
        bids=[Level(0.49, 500), Level(0.48, 800), Level(0.47, 1000)],
        asks=[Level(0.51, 500), Level(0.52, 800), Level(0.53, 1000)],
    )


@pytest.fixture
def wide_book() -> Book:
    """A wide book (6+ ticks) to exercise the wide-spread quoting branch."""
    return Book(bids=[Level(0.40, 200)], asks=[Level(0.60, 200)])


@pytest.fixture
def thin_book() -> Book:
    """A book with almost no depth, to trip the depth guard."""
    return Book(bids=[Level(0.49, 1)], asks=[Level(0.51, 1)])


# ── Credential-gated fixtures (skip when absent) ──────────────


@pytest.fixture
def testnet_private_key() -> str:
    """Polygon Amoy key for tier-3 tests. Skips when unset."""
    key = os.environ.get("POLYMM_TESTNET_PRIVATE_KEY")
    if not key:
        pytest.skip("POLYMM_TESTNET_PRIVATE_KEY not set — testnet tests skipped")
    return key


@pytest.fixture
def mainnet_private_key() -> str:
    """Mainnet key. Never used by tier-1/2 tests."""
    key = os.environ.get("POLYMM_PRIVATE_KEY")
    if not key:
        pytest.skip("POLYMM_PRIVATE_KEY not set")
    return key
