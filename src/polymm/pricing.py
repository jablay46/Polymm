"""Book-walk sizing, price validation, and Polymarket fee model.

All money here is Decimal, never float. Prediction-market shares are priced
in 0..1 with a tick (0.01 or 0.001); binary float error would let a "flat"
arbitrage look profitable by a fraction of a cent, which is exactly the bug
that loses money live.

Fee model (matches the exchange's fee formula and the reference
implementation in evan-kolberg/prediction-market-backtesting):

    fee_per_share = fee_rate * price * (1 - price)

It peaks at price 0.5 and vanishes at the extremes, so a deep-in-the-money
share is nearly fee-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

ZERO = Decimal("0")
ONE = Decimal("1")


class PricingError(ValueError):
    """Raised when a book or price cannot be used for sizing."""


@dataclass(frozen=True)
class Level:
    """One resting order: ``size`` shares at ``price`` USDC per share."""

    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        if self.size < ZERO:
            raise PricingError(f"level size must be >= 0, got {self.size}")


@dataclass(frozen=True)
class BookWalk:
    """Result of consuming a book side to fill a target size."""

    filled: Decimal
    notional: Decimal
    levels_used: int

    @property
    def avg_price(self) -> Decimal:
        if self.filled == ZERO:
            return ZERO
        return self.notional / self.filled

    @property
    def worst_price(self) -> Decimal:
        """Filled notional divided by filled size — i.e. the average.

        Kept as an explicit name because callers reason about "worst price
        paid" and the average is exactly that after a full walk.
        """
        return self.avg_price


def _as_decimal(value: object, *, name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PricingError(f"{name} is not a number: {value!r}") from exc


def validate_price(price: object, *, name: str = "price") -> Decimal:
    """Return ``price`` as a Decimal in the open interval (0, 1).

    Polymarket outcome shares are strictly between 0 and 1; 0 and 1 are not
    valid resting prices.
    """
    value = _as_decimal(price, name=name)
    if value <= ZERO or value >= ONE:
        raise PricingError(f"{name} must be in (0, 1), got {value}")
    return value


def tick_round(price: object, tick: object, *, mode: str = "down") -> Decimal:
    """Snap ``price`` to the nearest tick at or below/above it.

    ``mode="nearest"`` uses banker's rounding, matching py-clob-client's
    ``round_normal`` (an exact half snaps to the even tick).
    """
    value = _as_decimal(price, name="price")
    tick_size = _as_decimal(tick, name="tick")
    if tick_size <= ZERO:
        raise PricingError(f"tick must be > 0, got {tick_size}")
    if mode not in {"down", "up", "nearest"}:
        raise PricingError(f"unknown rounding mode {mode!r}")
    steps = value / tick_size
    if mode == "down":
        snapped = steps.to_integral_value(rounding=ROUND_DOWN)
    elif mode == "up":
        from decimal import ROUND_CEILING

        snapped = steps.to_integral_value(rounding=ROUND_CEILING)
    else:
        snapped = steps.to_integral_value()
    return snapped * tick_size


def walk_book(
    levels: list[Level],
    target_size: object,
    *,
    validate: bool = True,
) -> BookWalk:
    """Consume ``levels`` (best-first) to fill ``target_size`` shares.

    Returns the *achievable* fill. If the book is thinner than the target,
    ``filled`` is smaller than ``target_size`` — the caller decides whether
    a partial fill is acceptable. Levels are consumed in order, so a
    multi-level walk yields the true volume-weighted average price.
    """
    target = _as_decimal(target_size, name="target_size")
    if target < ZERO:
        raise PricingError(f"target_size must be >= 0, got {target}")

    remaining = target
    filled = ZERO
    notional = ZERO
    used = 0

    for level in levels:
        if remaining <= ZERO:
            break
        price = validate_price(level.price, name="level price") if validate else level.price
        take = min(remaining, level.size)
        if take <= ZERO:
            continue
        filled += take
        notional += take * price
        remaining -= take
        used += 1

    return BookWalk(filled=filled, notional=notional, levels_used=used)


def fee_per_share(price: object, fee_rate: object) -> Decimal:
    """Polymarket fee per share: ``fee_rate * price * (1 - price)``."""
    p = _as_decimal(price, name="price")
    rate = _as_decimal(fee_rate, name="fee_rate")
    if rate < ZERO:
        raise PricingError(f"fee_rate must be >= 0, got {rate}")
    return rate * p * (ONE - p)


def complete_set_cost(
    yes_ask: object,
    no_ask: object,
    *,
    yes_fee_rate: object = ZERO,
    no_fee_rate: object = ZERO,
) -> Decimal:
    """Net cost to acquire one YES + one NO share (settles to exactly 1.00).

    Includes taker fees on both legs. Below 1.00 (by more than a safety
    margin) is the arbitrage signal.
    """
    yes = validate_price(yes_ask, name="yes_ask")
    no = validate_price(no_ask, name="no_ask")
    return yes + no + fee_per_share(yes, yes_fee_rate) + fee_per_share(no, no_fee_rate)
