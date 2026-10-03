"""Tests for book-walk sizing and the fee model (task 0.5.6)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymm.pricing import (
    BookWalk,
    Level,
    PricingError,
    complete_set_cost,
    fee_per_share,
    tick_round,
    validate_price,
    walk_book,
)

pytestmark = pytest.mark.unit


def D(x: str) -> Decimal:
    return Decimal(x)


# ── validate_price ────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["0", "1", "-0.1", "1.5"])
def test_validate_price_rejects_out_of_range(bad: str) -> None:
    with pytest.raises(PricingError):
        validate_price(bad)


@pytest.mark.parametrize("good", ["0.01", "0.5", "0.999"])
def test_validate_price_accepts_in_range(good: str) -> None:
    assert validate_price(good) == D(good)


def test_validate_price_rejects_garbage() -> None:
    with pytest.raises(PricingError, match="not a number"):
        validate_price("not-a-price")


# ── walk_book ─────────────────────────────────────────────────


def test_walk_single_level() -> None:
    walk = walk_book([Level(D("0.40"), D("100"))], "50")
    assert walk.filled == D("50")
    assert walk.notional == D("20.00")
    assert walk.avg_price == D("0.4")
    assert walk.levels_used == 1


def test_walk_multiple_levels_volume_weighted() -> None:
    book = [
        Level(D("0.40"), D("10")),
        Level(D("0.42"), D("10")),
        Level(D("0.50"), D("100")),
    ]
    walk = walk_book(book, "25")
    # 10 @ 0.40 + 10 @ 0.42 + 5 @ 0.50 = 4 + 4.2 + 2.5 = 10.7 over 25 shares
    assert walk.filled == D("25")
    assert walk.notional == D("10.70")
    assert walk.avg_price == D("10.70") / D("25")
    assert walk.levels_used == 3


def test_walk_thin_book_returns_partial_fill() -> None:
    walk = walk_book([Level(D("0.40"), D("5"))], "50")
    assert walk.filled == D("5")
    assert walk.notional == D("2.00")
    assert walk.levels_used == 1


def test_walk_empty_book_is_zero() -> None:
    walk = walk_book([], "50")
    assert walk == BookWalk(filled=D("0"), notional=D("0"), levels_used=0)
    assert walk.avg_price == D("0")


def test_walk_rejects_negative_target() -> None:
    with pytest.raises(PricingError, match="target_size"):
        walk_book([Level(D("0.4"), D("1"))], "-1")


def test_walk_skips_zero_size_levels() -> None:
    book = [Level(D("0.40"), D("0")), Level(D("0.45"), D("10"))]
    walk = walk_book(book, "10")
    assert walk.filled == D("10")
    assert walk.notional == D("4.50")
    assert walk.levels_used == 1


def test_walk_validates_level_price_by_default() -> None:
    with pytest.raises(PricingError):
        walk_book([Level(D("1.2"), D("10"))], "5")


def test_walk_can_skip_validation_for_marked_prices() -> None:
    walk = walk_book([Level(D("1.2"), D("10"))], "5", validate=False)
    assert walk.filled == D("5")


def test_level_rejects_negative_size() -> None:
    with pytest.raises(PricingError, match="size"):
        Level(D("0.4"), D("-1"))


# ── tick rounding ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "price,tick,mode,expected",
    [
        ("0.1234", "0.01", "down", "0.12"),
        ("0.1234", "0.01", "up", "0.13"),
        # "nearest" uses banker's rounding, matching py-clob-client's
        # round_normal: an exact half snaps to the even tick.
        ("0.1250", "0.01", "nearest", "0.12"),
        ("0.1350", "0.01", "nearest", "0.14"),
        ("0.1234", "0.001", "down", "0.123"),
        ("0.5", "0.01", "down", "0.50"),
    ],
)
def test_tick_round(price: str, tick: str, mode: str, expected: str) -> None:
    assert tick_round(price, tick, mode=mode) == D(expected)


def test_tick_round_rejects_bad_tick() -> None:
    with pytest.raises(PricingError, match="tick"):
        tick_round("0.5", "0")


def test_tick_round_rejects_unknown_mode() -> None:
    with pytest.raises(PricingError, match="rounding mode"):
        tick_round("0.5", "0.01", mode="sideways")


# ── fee model ─────────────────────────────────────────────────


def test_fee_matches_reference_formula() -> None:
    # fee_rate * p * (1 - p), the exchange's formula.
    assert fee_per_share("0.5", "0.02") == D("0.02") * D("0.5") * D("0.5")
    assert fee_per_share("0.5", "0.02") == D("0.005")


def test_fee_is_symmetric_around_half() -> None:
    assert fee_per_share("0.3", "0.02") == fee_per_share("0.7", "0.02")


def test_fee_max_at_half() -> None:
    assert fee_per_share("0.5", "0.02") > fee_per_share("0.9", "0.02")
    assert fee_per_share("0.5", "0.02") > fee_per_share("0.1", "0.02")


def test_fee_zero_rate_is_zero() -> None:
    assert fee_per_share("0.5", "0") == D("0")


def test_fee_rejects_negative_rate() -> None:
    with pytest.raises(PricingError, match="fee_rate"):
        fee_per_share("0.5", "-0.01")


# ── complete set ──────────────────────────────────────────────


def test_complete_set_cost_sums_legs_and_fees() -> None:
    # 0.48 + 0.49 + fee(0.48) + fee(0.49), rate 0.02
    cost = complete_set_cost("0.48", "0.49", yes_fee_rate="0.02", no_fee_rate="0.02")
    expected = (
        D("0.48")
        + D("0.49")
        + D("0.02") * D("0.48") * D("0.52")
        + D("0.02") * D("0.49") * D("0.51")
    )
    assert cost == expected


def test_complete_set_cost_no_fees() -> None:
    assert complete_set_cost("0.48", "0.49") == D("0.97")


def test_complete_set_cost_validates_legs() -> None:
    with pytest.raises(PricingError):
        complete_set_cost("0", "0.5")


def test_profitable_arb_signal_math() -> None:
    # YES 0.47 + NO 0.48 = 0.95 gross; fees tiny at these prices.
    cost = complete_set_cost("0.47", "0.48", yes_fee_rate="0.02", no_fee_rate="0.02")
    assert cost < D("1")
    assert D("1") - cost > D("0.04")


def test_no_arb_when_sum_exceeds_one() -> None:
    cost = complete_set_cost("0.55", "0.55")
    assert cost > D("1")
