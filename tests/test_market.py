"""Tests for the order-book model (task 0.5.8)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymm.market import OrderBook
from polymm.pricing import PricingError

pytestmark = pytest.mark.unit


def D(x: str) -> Decimal:
    return Decimal(x)


def summary(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "asset_id": "123",
        "bids": [{"price": "0.48", "size": "100"}, {"price": "0.50", "size": "50"}],
        "asks": [{"price": "0.53", "size": "80"}, {"price": "0.51", "size": "60"}],
        "tick_size": "0.01",
        "min_order_size": "5",
        "neg_risk": False,
    }
    base.update(overrides)
    return base


def test_levels_are_sorted_best_first() -> None:
    book = OrderBook.from_clob(summary())
    # bids descending, asks ascending even though the feed was unsorted
    assert [lv.price for lv in book.bids] == [D("0.50"), D("0.48")]
    assert [lv.price for lv in book.asks] == [D("0.51"), D("0.53")]
    assert book.best_bid == D("0.50")
    assert book.best_ask == D("0.51")


def test_mid_and_spread() -> None:
    book = OrderBook.from_clob(summary())
    assert book.mid == D("0.505")
    assert book.spread == D("0.01")


def test_tick_and_min_size_parsed() -> None:
    book = OrderBook.from_clob(summary())
    assert book.tick_size == D("0.01")
    assert book.min_order_size == D("5")


def test_defaults_when_tick_missing() -> None:
    book = OrderBook.from_clob(summary(tick_size=None, min_order_size=None))
    assert book.tick_size == D("0.01")
    assert book.min_order_size == D("0")


def test_neg_risk_is_carried() -> None:
    assert OrderBook.from_clob(summary(neg_risk=True)).neg_risk is True
    assert OrderBook.from_clob(summary(neg_risk=False)).neg_risk is False


def test_accepts_dataclass_objects() -> None:
    from types import SimpleNamespace

    obj = SimpleNamespace(
        asset_id="9",
        bids=[SimpleNamespace(price="0.4", size="10")],
        asks=[SimpleNamespace(price="0.6", size="10")],
        tick_size="0.01",
        min_order_size="1",
        neg_risk=True,
        timestamp="t",
    )
    book = OrderBook.from_clob(obj)
    assert book.token_id == "9"
    assert book.best_bid == D("0.4")
    assert book.neg_risk is True


def test_empty_book_has_no_top() -> None:
    book = OrderBook.from_clob(summary(bids=[], asks=[]))
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.mid is None
    assert book.spread is None
    assert book.is_crossed is False


def test_one_sided_book() -> None:
    book = OrderBook.from_clob(summary(bids=[], asks=[{"price": "0.5", "size": "10"}]))
    assert book.best_ask == D("0.5")
    assert book.mid is None


def test_crossed_book_detected() -> None:
    book = OrderBook.from_clob(
        summary(
            bids=[{"price": "0.55", "size": "10"}],
            asks=[{"price": "0.50", "size": "10"}],
        )
    )
    assert book.is_crossed is True


def test_levels_for_side() -> None:
    book = OrderBook.from_clob(summary())
    assert book.levels_for("BUY") == book.asks
    assert book.levels_for("SELL") == book.bids
    with pytest.raises(PricingError, match="side"):
        book.levels_for("SIDEWAYS")


def test_depth_usd() -> None:
    book = OrderBook.from_clob(summary())
    # asks: 0.51*60 + 0.53*80 = 30.6 + 42.4 = 73.0
    assert book.depth_usd("BUY", 2) == D("73.0")
    assert book.depth_usd("BUY", 1) == D("30.6")
    # bids: 0.50*50 + 0.48*100 = 25 + 48 = 73.0
    assert book.depth_usd("SELL", 2) == D("73.0")


def test_bad_price_raises() -> None:
    with pytest.raises(PricingError, match="price is not a number"):
        OrderBook.from_clob(summary(bids=[{"price": "abc", "size": "1"}]))
