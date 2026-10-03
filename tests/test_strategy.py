"""Tests for the complete-set arbitrage strategy (task 0.5.11)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymm.market import Market, OrderBook
from polymm.pricing import Level
from polymm.strategy import ArbConfig, evaluate_market

pytestmark = pytest.mark.unit


def D(x: str) -> Decimal:
    return Decimal(x)


def market() -> Market:
    return Market(condition_id="0xcond", yes_token_id="YES", no_token_id="NO")


def book(
    token_id: str, asks: list[tuple[str, str]], bids: list[tuple[str, str]] | None = None
) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        bids=tuple(Level(D(p), D(s)) for p, s in (bids or [])),
        asks=tuple(Level(D(p), D(s)) for p, s in asks),
        tick_size=D("0.01"),
        min_order_size=D("1"),
    )


CFG = ArbConfig(fee_rate=D("0"))


def test_clear_arb_returns_intent() -> None:
    intent = evaluate_market(
        market(),
        book("YES", [("0.46", "100")]),
        book("NO", [("0.48", "100")]),
        CFG,
    )
    assert intent is not None
    assert intent.size == D("100")
    assert intent.gross_cost == D("0.94")
    assert intent.edge_per_share == D("0.06")
    assert intent.edge_usd == D("6.00")
    assert intent.yes_leg.token_id == "YES"
    assert intent.no_leg.token_id == "NO"
    assert intent.yes_leg.side == "BUY"


def test_no_arb_when_sum_at_or_above_one() -> None:
    assert (
        evaluate_market(
            market(),
            book("YES", [("0.55", "100")]),
            book("NO", [("0.55", "100")]),
            CFG,
        )
        is None
    )


def test_edge_below_minimum_rejected() -> None:
    # 0.495 + 0.495 = 0.99, edge 0.01; require 0.02.
    cfg = ArbConfig(fee_rate=D("0"), min_edge=D("0.02"))
    assert (
        evaluate_market(
            market(),
            book("YES", [("0.495", "100")]),
            book("NO", [("0.495", "100")]),
            cfg,
        )
        is None
    )


def test_fees_can_erase_a_gross_edge() -> None:
    # gross 0.94, but a punitive fee rate makes the basket cost >= 0.99.
    cfg = ArbConfig(fee_rate=D("0.30"), min_edge=D("0.01"))
    intent = evaluate_market(
        market(),
        book("YES", [("0.47", "100")]),
        book("NO", [("0.47", "100")]),
        cfg,
    )
    # fee each = 0.30*0.47*0.53 = 0.07473; net = 0.94 + 0.14946 = 1.089 -> no
    assert intent is None


def test_max_basket_cost_caps() -> None:
    cfg = ArbConfig(fee_rate=D("0"), max_basket_cost=D("0.90"))
    # net 0.94 > 0.90 -> rejected even though edge is large
    assert (
        evaluate_market(
            market(),
            book("YES", [("0.46", "100")]),
            book("NO", [("0.48", "100")]),
            cfg,
        )
        is None
    )


def test_max_leg_price_gate() -> None:
    cfg = ArbConfig(fee_rate=D("0"), max_leg_price=D("0.40"))
    assert (
        evaluate_market(
            market(),
            book("YES", [("0.46", "100")]),
            book("NO", [("0.48", "100")]),
            cfg,
        )
        is None
    )


def test_wide_spread_gate() -> None:
    cfg = ArbConfig(fee_rate=D("0"), max_spread=D("0.02"))
    # YES spread = 0.46 - 0.30 = 0.16 > 0.02
    assert (
        evaluate_market(
            market(),
            book("YES", [("0.46", "100")], bids=[("0.30", "100")]),
            book("NO", [("0.48", "100")], bids=[("0.47", "100")]),
            cfg,
        )
        is None
    )


def test_crossed_book_is_ignored() -> None:
    assert (
        evaluate_market(
            market(),
            book("YES", [("0.40", "100")], bids=[("0.50", "100")]),
            book("NO", [("0.48", "100")]),
            CFG,
        )
        is None
    )


def test_missing_ask_is_ignored() -> None:
    assert evaluate_market(market(), book("YES", []), book("NO", [("0.48", "100")]), CFG) is None


def test_size_capped_by_thinnest_side() -> None:
    cfg = ArbConfig(fee_rate=D("0"), target_size=D("100"))
    intent = evaluate_market(
        market(),
        book("YES", [("0.46", "30")]),
        book("NO", [("0.48", "100")]),
        cfg,
    )
    assert intent is not None
    assert intent.size == D("30")


def test_book_walk_uses_deep_levels() -> None:
    # Top ask 0.40 but only 10 shares; next 0.45 for the rest.
    cfg = ArbConfig(fee_rate=D("0"), target_size=D("20"))
    intent = evaluate_market(
        market(),
        book("YES", [("0.40", "10"), ("0.45", "100")]),
        book("NO", [("0.45", "100")]),
        cfg,
    )
    assert intent is not None
    assert intent.size == D("20")
    # YES avg = (10*0.40 + 10*0.45)/20 = 0.425
    assert intent.yes_leg.price == D("0.425")
    assert intent.gross_cost == D("0.425") + D("0.45")


def test_insufficient_depth_for_target_is_rejected() -> None:
    # Only 5 shares available but min_visible_size is 1 and target 100:
    # size is capped to 5, which is fine; but if visible < min, reject.
    cfg = ArbConfig(fee_rate=D("0"), min_visible_size=D("10"))
    assert (
        evaluate_market(
            market(),
            book("YES", [("0.46", "5")]),
            book("NO", [("0.48", "100")]),
            cfg,
        )
        is None
    )


def test_market_requires_both_tokens() -> None:
    with pytest.raises(Exception, match="yes and no"):
        Market(condition_id="c", yes_token_id="X", no_token_id="X")
