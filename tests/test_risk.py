"""Tests for the risk manager and kill switch (task 0.5.7)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymm.config import RiskConfig
from polymm.risk import RiskLimits, RiskManager

pytestmark = pytest.mark.unit


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, secs: float) -> None:
        self.t += secs


def D(x: str) -> Decimal:
    return Decimal(x)


def make_manager(bankroll: str = "1000", **overrides: object) -> tuple[RiskManager, FakeClock]:
    cfg = RiskConfig(**overrides)  # type: ignore[arg-type]
    clock = FakeClock()
    mgr = RiskManager(RiskLimits.from_config(cfg), bankroll=bankroll, clock=clock)
    return mgr, clock


def ok_order(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "notional": "10",
        "size_shares": "20",
        "depth_usd": "5000",
    }
    base.update(overrides)
    return base


# ── happy path ────────────────────────────────────────────────


def test_small_order_allowed() -> None:
    mgr, _ = make_manager()
    assert mgr.evaluate_order(**ok_order()).allowed


# ── bankroll floor ────────────────────────────────────────────


def test_bankroll_below_minimum_denies() -> None:
    mgr, _ = make_manager(bankroll="10")  # min_bankroll_usd default 50
    d = mgr.evaluate_order(**ok_order())
    assert not d.allowed
    assert "below minimum" in d.reason


# ── per-order cap ─────────────────────────────────────────────


def test_order_notional_cap() -> None:
    # 5% of 1000 = 50 max per order.
    mgr, _ = make_manager()
    assert mgr.evaluate_order(**ok_order(notional="50")).allowed
    d = mgr.evaluate_order(**ok_order(notional="50.01"))
    assert not d.allowed
    assert "exceeds cap" in d.reason


def test_zero_notional_denied() -> None:
    mgr, _ = make_manager()
    d = mgr.evaluate_order(**ok_order(notional="0"))
    assert not d.allowed
    assert "positive" in d.reason


# ── total exposure cap ────────────────────────────────────────


def test_total_exposure_cap() -> None:
    # 50% of 1000 = 500 total.
    mgr, _ = make_manager()
    mgr.record_fill(notional=D("490"), open_position=False)
    assert mgr.evaluate_order(**ok_order(notional="10")).allowed
    d = mgr.evaluate_order(**ok_order(notional="11"))
    assert not d.allowed
    assert "total exposure" in d.reason


# ── open position cap ─────────────────────────────────────────


def test_open_position_cap() -> None:
    mgr, _ = make_manager(max_open_positions=2)
    mgr.record_fill(notional=D("1"), open_position=True)
    mgr.record_fill(notional=D("1"), open_position=True)
    d = mgr.evaluate_order(**ok_order())
    assert not d.allowed
    assert "open positions" in d.reason


# ── depth floor ───────────────────────────────────────────────


def test_depth_floor() -> None:
    mgr, _ = make_manager()
    d = mgr.evaluate_order(**ok_order(depth_usd="100"))  # min 200
    assert not d.allowed
    assert "depth" in d.reason


# ── daily loss kill switch ────────────────────────────────────


def test_daily_loss_limit_trips_kill_switch() -> None:
    mgr, _ = make_manager(daily_loss_limit_usd=25)
    mgr.record_close(notional=D("10"), pnl=D("-30"))
    assert mgr.is_halted()
    d = mgr.evaluate_order(**ok_order())
    assert not d.allowed
    assert "kill switch" in d.reason


def test_kill_switch_expires_after_duration() -> None:
    mgr, clock = make_manager(daily_loss_limit_usd=25, trip_duration_secs=60)
    mgr.record_close(notional=D("10"), pnl=D("-30"))
    assert mgr.is_halted()
    clock.advance(61)
    assert not mgr.is_halted()
    assert mgr.evaluate_order(**ok_order()).allowed


def test_daily_loss_resets_on_new_day() -> None:
    from datetime import date

    days = [date(2026, 1, 1)]

    def today() -> date:
        return days[-1]

    cfg = RiskConfig(daily_loss_limit_usd=25)
    mgr = RiskManager(RiskLimits.from_config(cfg), bankroll="1000", clock=FakeClock(), today=today)
    mgr.record_close(notional=D("10"), pnl=D("-20"))
    assert mgr.state.daily_pnl == D("-20")
    days.append(date(2026, 1, 2))  # new day rolls the counter
    mgr.record_close(notional=D("10"), pnl=D("-1"))
    assert mgr.state.daily_pnl == D("-1")
    assert not mgr.is_halted()


# ── large-trade burst ─────────────────────────────────────────


def test_large_trade_burst_trips() -> None:
    # large_trade_shares default 1000, consecutive_trigger default 3.
    mgr, _ = make_manager()
    assert mgr.evaluate_order(**ok_order(size_shares="1500")).allowed
    assert mgr.evaluate_order(**ok_order(size_shares="1500")).allowed
    d = mgr.evaluate_order(**ok_order(size_shares="1500"))
    assert not d.allowed
    assert "burst" in d.reason
    assert mgr.is_halted()


def test_large_trades_outside_window_do_not_trip() -> None:
    mgr, clock = make_manager(sequence_window_secs=30, consecutive_trigger=3)
    for _ in range(5):
        assert mgr.evaluate_order(**ok_order(size_shares="1500")).allowed
        clock.advance(31)  # each trade is its own window
    assert not mgr.is_halted()


def test_small_trades_never_trip_burst() -> None:
    mgr, _ = make_manager()
    for _ in range(100):
        assert mgr.evaluate_order(**ok_order(size_shares="10")).allowed
    assert not mgr.is_halted()


# ── state accounting ──────────────────────────────────────────


def test_record_close_reduces_exposure_and_positions() -> None:
    mgr, _ = make_manager()
    mgr.record_fill(notional=D("20"), open_position=True)
    mgr.record_close(notional=D("20"), pnl=D("1"))
    assert mgr.state.exposure == D("0")
    assert mgr.state.open_positions == 0
    assert mgr.state.realized_pnl == D("1")


def test_negative_bankroll_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="bankroll"):
        RiskManager(RiskLimits.from_config(RiskConfig()), bankroll="-1")
