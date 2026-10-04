"""Tests for the exchange wrapper and paper-trading venue (tasks 0.5.9/0.5.10).

The paper venue is tested as real code — it is the default trading path and
must be correct, not a throwaway mock. Book walking, fees, balance, and
position accounting are all exercised.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymm.exchange import ExchangeError, OrderRequest
from polymm.market import OrderBook
from polymm.paper import PaperExchange
from polymm.pricing import Level

pytestmark = pytest.mark.unit


def D(x: str) -> Decimal:
    return Decimal(x)


def book(
    token_id: str = "T",  # noqa: S107 - not a secret, just a test token id
    bids: list[tuple[str, str]] | None = None,
    asks: list[tuple[str, str]] | None = None,
    neg_risk: bool = False,
) -> OrderBook:
    bid_levels = [("0.48", "100")] if bids is None else bids
    ask_levels = [("0.52", "100")] if asks is None else asks
    return OrderBook(
        token_id=token_id,
        bids=tuple(Level(D(p), D(s)) for p, s in bid_levels),
        asks=tuple(Level(D(p), D(s)) for p, s in ask_levels),
        tick_size=D("0.01"),
        min_order_size=D("1"),
        neg_risk=neg_risk,
    )


# ── OrderRequest validation ───────────────────────────────────


def test_order_request_rejects_bad_side() -> None:
    with pytest.raises(ExchangeError, match="side"):
        OrderRequest(token_id="T", side="HOLD", price=D("0.5"), size=D("1"))


def test_order_request_rejects_nonpositive_size() -> None:
    with pytest.raises(ExchangeError, match="size"):
        OrderRequest(token_id="T", side="BUY", price=D("0.5"), size=D("0"))


# ── paper venue: marketable buy ───────────────────────────────


def test_marketable_buy_walks_levels_and_charges_fee() -> None:
    ex = PaperExchange(balance=D("1000"), fee_rate=D("0.02"))
    ex.set_book(book(asks=[("0.50", "10"), ("0.52", "10")]))

    result = ex.place_order(OrderRequest(token_id="T", side="BUY", price=D("0.55"), size=D("15")))
    assert result.status == "filled"
    assert result.filled_size == D("15")
    # 10 @ 0.50 + 5 @ 0.52 = 5 + 2.6 = 7.6 over 15 -> avg 0.506666...
    assert result.avg_price == D("7.6") / D("15")

    fee = D("0.02") * result.avg_price * (D("1") - result.avg_price) * D("15")
    assert ex.balance == D("1000") - D("7.6") - fee
    assert ex.position_size("T") == D("15")


def test_marketable_buy_only_fills_visible_size() -> None:
    ex = PaperExchange(balance=D("1000"))
    ex.set_book(book(asks=[("0.50", "4")]))
    result = ex.place_order(OrderRequest(token_id="T", side="BUY", price=D("0.60"), size=D("10")))
    # Only 4 shares are available; no phantom liquidity is invented.
    assert result.filled_size == D("4")


def test_fok_kills_instead_of_partial_filling() -> None:
    """FOK is all-or-nothing; the paper venue must not accept a partial.

    Regression: the paper venue used to book whatever the book could fill,
    so a strategy bug that assumed full fills was invisible in paper runs.
    """
    ex = PaperExchange(balance=D("1000"))
    ex.set_book(book(asks=[("0.50", "4")]))
    result = ex.place_order(
        OrderRequest(token_id="T", side="BUY", price=D("0.60"), size=D("10"), order_type="FOK")
    )
    assert result.status == "killed"
    assert result.filled_size == D("0")
    assert ex.balance == D("1000")
    assert ex.position_size("T") == D("0")


def test_fak_allows_partial_fill() -> None:
    ex = PaperExchange(balance=D("1000"))
    ex.set_book(book(asks=[("0.50", "4")]))
    result = ex.place_order(
        OrderRequest(token_id="T", side="BUY", price=D("0.60"), size=D("10"), order_type="FAK")
    )
    assert result.filled_size == D("4")


def test_order_below_market_minimum_is_rejected() -> None:
    ex = PaperExchange(balance=D("1000"))
    ex.set_book(book(asks=[("0.50", "100")]))
    with pytest.raises(ExchangeError, match="minimum"):
        ex.place_order(OrderRequest(token_id="T", side="BUY", price=D("0.60"), size=D("0.5")))


# ── paper venue: non-crossing limit order ─────────────────────


def test_non_crossing_buy_rests_unfilled() -> None:
    ex = PaperExchange(balance=D("1000"))
    ex.set_book(book(asks=[("0.52", "10")]))
    result = ex.place_order(OrderRequest(token_id="T", side="BUY", price=D("0.50"), size=D("10")))
    assert result.status == "resting"
    assert result.filled_size == D("0")
    assert ex.balance == D("1000")


# ── paper venue: sell ─────────────────────────────────────────


def test_sell_reduces_position_and_adds_proceeds() -> None:
    ex = PaperExchange(balance=D("1000"), fee_rate=D("0"))
    ex.set_book(book(asks=[("0.50", "100")], bids=[("0.55", "100")]))
    ex.place_order(OrderRequest(token_id="T", side="BUY", price=D("0.50"), size=D("10")))
    assert ex.position_size("T") == D("10")

    result = ex.place_order(OrderRequest(token_id="T", side="SELL", price=D("0.55"), size=D("10")))
    assert result.filled_size == D("10")
    assert ex.position_size("T") == D("0")
    assert ex.balance == D("1000") - D("5") + D("5.5")


def test_sell_without_position_raises() -> None:
    ex = PaperExchange(balance=D("1000"))
    ex.set_book(book(bids=[("0.55", "100")]))
    with pytest.raises(ExchangeError, match="insufficient shares"):
        ex.place_order(OrderRequest(token_id="T", side="SELL", price=D("0.50"), size=D("1")))


# ── paper venue: guards ───────────────────────────────────────


def test_insufficient_balance_raises() -> None:
    ex = PaperExchange(balance=D("1"))
    ex.set_book(book(asks=[("0.50", "100")]))
    with pytest.raises(ExchangeError, match="insufficient balance"):
        ex.place_order(OrderRequest(token_id="T", side="BUY", price=D("0.60"), size=D("10")))


def test_missing_book_raises() -> None:
    ex = PaperExchange()
    with pytest.raises(ExchangeError, match="no book"):
        ex.place_order(OrderRequest(token_id="MISSING", side="BUY", price=D("0.5"), size=D("1")))


def test_empty_side_raises() -> None:
    ex = PaperExchange()
    ex.set_book(book(asks=[]))
    with pytest.raises(ExchangeError, match="no BUY liquidity"):
        ex.place_order(OrderRequest(token_id="T", side="BUY", price=D("0.5"), size=D("1")))


def test_balance_usdc_reports() -> None:
    assert PaperExchange(balance=D("42")).balance_usdc() == D("42")


def test_cancel_is_noop_but_succeeds() -> None:
    ex = PaperExchange()
    assert ex.cancel("anything") is True
    ex.cancel_all()


# ── full round trip: buy the set, settle at 1.00 ──────────────


def test_complete_set_round_trip_profit() -> None:
    """Buy YES + NO cheaply, settle to exactly 1.00 per set, book the edge."""
    ex = PaperExchange(balance=D("100"), fee_rate=D("0.02"))
    ex.set_book(book(token_id="YES", asks=[("0.46", "100")], bids=[]))
    ex.set_book(book(token_id="NO", asks=[("0.48", "100")], bids=[]))

    ex.place_order(OrderRequest(token_id="YES", side="BUY", price=D("0.46"), size=D("10")))
    ex.place_order(OrderRequest(token_id="NO", side="BUY", price=D("0.48"), size=D("10")))

    spent = D("100") - ex.balance
    # A complete set of 10 settles to 10.00 USDC.
    assert D("10") - spent > D("0.5")
    assert ex.position_size("YES") == D("10")
    assert ex.position_size("NO") == D("10")


# ── live adapter boundary, exercised with a fake client ───────


class FakeClobClient:
    """Records what the adapter sends; returns canned API shapes."""

    def __init__(self, *, book: dict | None = None, fail: bool = False) -> None:
        self._book = book or {
            "asset_id": "T",
            "bids": [{"price": "0.48", "size": "100"}],
            "asks": [{"price": "0.51", "size": "100"}],
            "tick_size": "0.01",
            "min_order_size": "1",
        }
        self._fail = fail
        self.created: list[object] = []
        self.posted: list[object] = []
        self.cancelled: list[str] = []

    def get_order_book(self, token_id: str) -> dict:
        if self._fail:
            raise RuntimeError("boom")
        return self._book

    def create_order(self, args: object, options: object) -> dict:
        self.created.append((args, options))
        return {"order": "signed"}

    def post_order(self, signed: object, order_type: object) -> dict:
        self.posted.append((signed, order_type))
        return {"orderID": "0xabc", "status": "matched"}

    def cancel(self, order_id: str) -> None:
        self.cancelled.append(order_id)

    def cancel_all(self) -> None:
        self.cancelled.append("*")

    def get_balance_allowance(self, params: object) -> dict:
        return {"balance": "12345678"}  # 12.345678 USDC


def test_clob_get_order_book_parses() -> None:
    from polymm.exchange import ClobExchange

    ex = ClobExchange(FakeClobClient())
    b = ex.get_order_book("T")
    assert b.best_bid == D("0.48")
    assert b.best_ask == D("0.51")


def test_clob_get_order_book_wraps_errors() -> None:
    from polymm.exchange import ClobExchange

    ex = ClobExchange(FakeClobClient(fail=True))
    with pytest.raises(ExchangeError, match="get_order_book failed"):
        ex.get_order_book("T")


def test_clob_place_order_snaps_price_to_tick_and_posts() -> None:
    pytest.importorskip("py_clob_client")
    from polymm.exchange import ClobExchange

    client = FakeClobClient()
    ex = ClobExchange(client, live_allowed=True)
    result = ex.place_order(OrderRequest(token_id="T", side="BUY", price=D("0.519"), size=D("10")))
    assert result.order_id == "0xabc"
    assert result.status == "matched"
    args, options = client.created[0]
    # A BUY limit is rounded *up* to the 0.01 tick: rounding down could put
    # the limit below the level we need and stop the order filling.
    assert args.price == pytest.approx(0.52)
    assert options.neg_risk is False
    assert client.posted[0][0] == {"order": "signed"}


def test_order_type_resolved_by_attribute_not_subscript() -> None:
    """Regression for K2.

    py-clob-client's ``OrderType`` is a plain class of string attributes, not
    an enum: ``OrderType["FOK"]`` returns a GenericAlias that cannot be
    serialised, so every live order failed. Resolution must use attributes.
    """
    pytest.importorskip("py_clob_client")
    from py_clob_client.clob_types import OrderType

    from polymm.exchange import _resolve_order_type

    assert _resolve_order_type(OrderType, "FOK") == "FOK"
    assert _resolve_order_type(OrderType, "GTC") == "GTC"
    assert _resolve_order_type(OrderType, "IOC") == "FAK"  # documented alias


def test_order_type_rejects_unknown() -> None:
    from polymm.exchange import ExchangeError, _resolve_order_type

    class Fake:
        FOK = "FOK"

    with pytest.raises(ExchangeError, match="unsupported order type"):
        _resolve_order_type(Fake, "NOPE")


def test_clob_place_order_blocked_when_live_gate_closed() -> None:
    pytest.importorskip("py_clob_client")
    from polymm.exchange import ClobExchange

    client = FakeClobClient()
    ex = ClobExchange(client)  # live_allowed defaults to False
    with pytest.raises(ExchangeError, match="gate is closed"):
        ex.place_order(OrderRequest(token_id="T", side="BUY", price=D("0.5"), size=D("1")))
    assert client.posted == []


def test_clob_place_order_wraps_errors() -> None:
    pytest.importorskip("py_clob_client")
    from polymm.exchange import ClobExchange

    ex = ClobExchange(FakeClobClient(fail=True), live_allowed=True)
    with pytest.raises(ExchangeError, match="get_order_book failed"):
        ex.place_order(OrderRequest(token_id="T", side="BUY", price=D("0.5"), size=D("1")))


def test_clob_cancel_and_cancel_all() -> None:
    from polymm.exchange import ClobExchange

    client = FakeClobClient()
    ex = ClobExchange(client)
    assert ex.cancel("0x1") is True
    ex.cancel_all()
    assert client.cancelled == ["0x1", "*"]


def test_clob_balance_scales_from_six_decimals() -> None:
    pytest.importorskip("py_clob_client")
    from polymm.exchange import ClobExchange

    ex = ClobExchange(FakeClobClient())
    assert ex.balance_usdc() == D("12.345678")


def test_clob_balance_wraps_errors() -> None:
    pytest.importorskip("py_clob_client")
    from polymm.exchange import ClobExchange

    class BadClient(FakeClobClient):
        def get_balance_allowance(self, params: object) -> dict:
            raise RuntimeError("nope")

    with pytest.raises(ExchangeError, match="balance fetch failed"):
        ClobExchange(BadClient()).balance_usdc()
