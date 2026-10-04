"""Tests for two-leg execution and non-atomic-fill handling (task 0.5.12)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymm.config import RiskConfig
from polymm.exchange import ExchangeError, OrderRequest, OrderResult
from polymm.execution import (
    ExecutionConfig,
    ExecutionStatus,
    execute_intent,
)
from polymm.market import Market, OrderBook
from polymm.pricing import Level
from polymm.risk import RiskLimits, RiskManager
from polymm.strategy import ArbConfig, ArbIntent, evaluate_market

pytestmark = pytest.mark.unit


def D(x: str) -> Decimal:
    return Decimal(x)


def mk_book(token_id: str, asks, bids) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        bids=tuple(Level(D(p), D(s)) for p, s in bids),
        asks=tuple(Level(D(p), D(s)) for p, s in asks),
        tick_size=D("0.01"),
        min_order_size=D("1"),
    )


class ScriptedExchange:
    """Exchange whose responses are queued per (token_id, side)."""

    def __init__(self) -> None:
        self.books: dict[str, OrderBook] = {}
        self.responses: dict[tuple[str, str], list] = {}
        self.calls: list[OrderRequest] = []

    def set_book(self, book: OrderBook) -> None:
        self.books[book.token_id] = book

    def script(self, token_id: str, side: str, *responses) -> None:
        self.responses[(token_id, side)] = list(responses)

    def get_order_book(self, token_id: str) -> OrderBook:
        return self.books[token_id]

    def place_order(self, request: OrderRequest) -> OrderResult:
        self.calls.append(request)
        key = (request.token_id, request.side.upper())
        queue = self.responses.get(key)
        if not queue:
            raise ExchangeError(f"no scripted response for {key}")
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def cancel(self, order_id: str) -> bool:
        return True

    def cancel_all(self) -> None:
        return None

    def balance_usdc(self) -> Decimal:
        return D("1000")


def filled(order_id: str, size: str, price: str) -> OrderResult:
    return OrderResult(order_id=order_id, status="filled", filled_size=D(size), avg_price=D(price))


def intent_for(yes_ask: str, no_ask: str, size: str = "100") -> ArbIntent:
    mkt = Market(condition_id="c", yes_token_id="YES", no_token_id="NO")
    # Bids set 0.02 below each ask so the default spread gate passes; the
    # strategy only looks at asks for the arbitrage itself.
    yb = mk_book("YES", [(yes_ask, "500")], [(str(D(yes_ask) - D("0.02")), "500")])
    nb = mk_book("NO", [(no_ask, "500")], [(str(D(no_ask) - D("0.02")), "500")])
    cfg = ArbConfig(fee_rate=D("0"), target_size=D(size))
    it = evaluate_market(mkt, yb, nb, cfg)
    assert it is not None
    return it


def risk_mgr(**overrides) -> RiskManager:
    # Caps are sized so a 2-leg basket (~0.94 * 100) fits: the executor now
    # checks the *whole basket* against the per-order cap, not each leg.
    params = {"max_order_bankroll_fraction": 0.5, "max_total_bankroll_fraction": 1.0}
    params.update(overrides)
    cfg = RiskConfig(**params)  # type: ignore[arg-type]
    return RiskManager(RiskLimits.from_config(cfg), bankroll=D("1000"))


def setup_exchange(intent: ArbIntent) -> ScriptedExchange:
    ex = ScriptedExchange()
    ex.set_book(mk_book("YES", [("0.46", "500")], [("0.40", "500")]))
    ex.set_book(mk_book("NO", [("0.48", "500")], [("0.40", "500")]))
    return ex


# ── happy path ────────────────────────────────────────────────


def test_both_legs_fill_locks_edge() -> None:
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.script("YES", "BUY", filled("y1", "100", "0.46"))
    ex.script("NO", "BUY", filled("n1", "100", "0.48"))

    result = execute_intent(ex, intent, risk_mgr())
    assert result.status is ExecutionStatus.FILLED
    assert result.success
    assert result.realized_edge_usd == D("6.00")
    assert not result.holds_naked_position


def test_cheaper_leg_is_bought_first() -> None:
    # NO (0.48) is cheaper than YES (0.46)? No: 0.46 < 0.48, so YES first.
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.script("YES", "BUY", filled("y1", "100", "0.46"))
    ex.script("NO", "BUY", filled("n1", "100", "0.48"))
    execute_intent(ex, intent, risk_mgr())
    assert ex.calls[0].token_id == "YES"

    # Now make NO the cheaper leg.
    intent2 = intent_for("0.44", "0.40")
    ex2 = ScriptedExchange()
    ex2.set_book(mk_book("YES", [("0.44", "500")], [("0.40", "500")]))
    ex2.set_book(mk_book("NO", [("0.40", "500")], [("0.35", "500")]))
    ex2.script("NO", "BUY", filled("n1", "100", "0.40"))
    ex2.script("YES", "BUY", filled("y1", "100", "0.44"))
    execute_intent(ex2, intent2, risk_mgr())
    assert ex2.calls[0].token_id == "NO"


# ── abort paths ───────────────────────────────────────────────


def test_risk_denies_first_leg_aborts_cleanly() -> None:
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    # kill the account first
    mgr = risk_mgr(daily_loss_limit_usd=1)
    mgr.record_close(notional=D("1"), pnl=D("-5"))

    result = execute_intent(ex, intent, mgr)
    assert result.status is ExecutionStatus.ABORTED
    assert ex.calls == []
    assert not result.holds_naked_position


def test_first_leg_rejected_aborts() -> None:
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.script("YES", "BUY", ExchangeError("venue down"))
    result = execute_intent(ex, intent, risk_mgr())
    assert result.status is ExecutionStatus.ABORTED
    assert not result.holds_naked_position


def test_first_leg_partial_fill_aborts_without_exposure() -> None:
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.script("YES", "BUY", filled("y1", "40", "0.46"))  # wanted 100
    result = execute_intent(ex, intent, risk_mgr())
    assert result.status is ExecutionStatus.ABORTED
    assert result.first_leg is not None
    assert result.first_leg.filled_size == D("40")


# ── one-legged fill: the critical case ────────────────────────


def test_second_leg_rejected_unwinds_first() -> None:
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.script("YES", "BUY", filled("y1", "100", "0.46"))
    ex.script("NO", "BUY", ExchangeError("no liquidity"))
    # unwind sells YES at its best bid (0.40)
    ex.script("YES", "SELL", filled("s1", "100", "0.40"))

    result = execute_intent(ex, intent, risk_mgr())
    assert result.status is ExecutionStatus.UNWOUND
    assert not result.holds_naked_position
    # loss = (0.46 - 0.40) * 100 = 6.00
    assert result.realized_edge_usd == D("-6.00")
    assert ex.calls[-1].side == "SELL"


def test_second_leg_partial_fill_unwinds() -> None:
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.script("YES", "BUY", filled("y1", "100", "0.46"))
    ex.script("NO", "BUY", filled("n1", "30", "0.48"))  # partial
    ex.script("YES", "SELL", filled("s1", "100", "0.40"))
    result = execute_intent(ex, intent, risk_mgr())
    assert result.status is ExecutionStatus.UNWOUND


def test_unwind_failure_flags_unhedged() -> None:
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.script("YES", "BUY", filled("y1", "100", "0.46"))
    ex.script("NO", "BUY", ExchangeError("no liquidity"))
    ex.script("YES", "SELL", ExchangeError("unwind rejected"))

    result = execute_intent(ex, intent, risk_mgr())
    assert result.status is ExecutionStatus.UNHEDGED
    assert result.holds_naked_position
    assert "UNWIND FAILED" in result.note


def test_no_bid_to_unwind_flags_unhedged() -> None:
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.books["YES"] = mk_book("YES", [("0.46", "500")], [])  # no bids
    ex.script("YES", "BUY", filled("y1", "100", "0.46"))
    ex.script("NO", "BUY", ExchangeError("no liquidity"))
    result = execute_intent(ex, intent, risk_mgr())
    assert result.status is ExecutionStatus.UNHEDGED
    assert result.holds_naked_position


def test_unwind_disabled_holds_naked() -> None:
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.script("YES", "BUY", filled("y1", "100", "0.46"))
    ex.script("NO", "BUY", ExchangeError("no liquidity"))
    cfg = ExecutionConfig(unwind_on_failure=False)
    result = execute_intent(ex, intent, risk_mgr(), cfg)
    assert result.status is ExecutionStatus.UNHEDGED


def test_risk_denies_oversized_basket_before_any_order() -> None:
    # The basket is checked as a whole up front. A cap that each leg would
    # pass but the pair would not must deny *before* leg 1 leaves.
    intent = intent_for("0.46", "0.48")  # basket notional ~94
    ex = setup_exchange(intent)
    mgr = risk_mgr(max_order_bankroll_fraction=0.05)  # cap 50 < 94
    result = execute_intent(ex, intent, mgr)
    assert result.status is ExecutionStatus.ABORTED
    assert ex.calls == []
    assert "risk denied basket" in result.note
    assert not result.holds_naked_position


def test_second_book_error_aborts_before_first_leg() -> None:
    # A transient error reading the *second* book must abort before any
    # exposure is taken, not after leg 1 has filled.
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.script("YES", "BUY", filled("y1", "100", "0.46"))

    real = ex.get_order_book

    def flaky(token_id: str) -> OrderBook:
        if token_id == "NO":
            raise ExchangeError("network blip")
        return real(token_id)

    ex.get_order_book = flaky  # type: ignore[method-assign]

    result = execute_intent(ex, intent, risk_mgr())
    assert result.status is ExecutionStatus.ABORTED
    assert ex.calls == []
    assert "book unavailable" in result.note


def test_partial_unwind_flags_unhedged() -> None:
    intent = intent_for("0.46", "0.48")
    ex = setup_exchange(intent)
    ex.script("YES", "BUY", filled("y1", "100", "0.46"))
    ex.script("NO", "BUY", ExchangeError("no liquidity"))
    ex.script("YES", "SELL", filled("s1", "60", "0.40"))  # only 60 of 100
    result = execute_intent(ex, intent, risk_mgr())
    assert result.status is ExecutionStatus.UNHEDGED
    assert "partially filled" in result.note
