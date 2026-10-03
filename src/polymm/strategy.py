"""Complete-set arbitrage strategy.

The core, safest edge on Polymarket: in a binary market one YES share plus
one NO share always settles to exactly 1.00 USDC. If both can be bought for
less than 1.00 *after taker fees and slippage*, the difference is a locked
profit held to resolution.

This strategy is pure and synchronous: given two books and a config it
returns an intent (or ``None``). It does no I/O, so it is fully unit-tested.
The runner is responsible for risk checks and execution.

Why this and not market making? It needs no inventory, no forecasting, no
speed edge, and no opinion about the outcome — the profit is arithmetic.
The main risks are operational (a one-legged fill), which the runner
handles by buying the cheap leg first and unwinding if the second leg fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from polymm.market import Market, OrderBook
from polymm.pricing import fee_per_share, walk_book

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class ArbConfig:
    min_edge: Decimal = Decimal("0.01")
    max_basket_cost: Decimal = Decimal("0.99")
    max_leg_price: Decimal = Decimal("0.985")
    max_spread: Decimal = Decimal("0.08")
    fee_rate: Decimal = Decimal("0.02")
    target_size: Decimal = Decimal("100")
    min_visible_size: Decimal = Decimal("1")


@dataclass(frozen=True)
class ArbLeg:
    token_id: str
    side: str
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class ArbIntent:
    market: Market
    legs: tuple[ArbLeg, ArbLeg]
    size: Decimal
    gross_cost: Decimal
    net_cost: Decimal
    edge_per_share: Decimal
    edge_usd: Decimal

    @property
    def yes_leg(self) -> ArbLeg:
        return self.legs[0]

    @property
    def no_leg(self) -> ArbLeg:
        return self.legs[1]


def _avg_buy_price(book: OrderBook, size: Decimal) -> Decimal | None:
    """Volume-weighted ask price to buy ``size`` shares, or None if too thin."""
    if size <= ZERO or not book.asks:
        return None
    walk = walk_book(list(book.asks), size)
    if walk.filled < size:
        return None
    return walk.avg_price


def evaluate_market(
    market: Market,
    yes_book: OrderBook,
    no_book: OrderBook,
    config: ArbConfig,
) -> ArbIntent | None:
    """Return a complete-set arbitrage intent, or ``None`` if there is no edge.

    Buying ``size`` YES and ``size`` NO locks ``size * 1.00`` at resolution.
    The intent is only returned when the *executable* cost (walking the real
    books, including taker fees) is below ``1 - min_edge``.
    """
    if yes_book.is_crossed or no_book.is_crossed:
        return None
    if yes_book.best_ask is None or no_book.best_ask is None:
        return None

    # Cheap sanity gates before the (slightly more expensive) book walk.
    if yes_book.best_ask > config.max_leg_price:
        return None
    if no_book.best_ask > config.max_leg_price:
        return None
    if yes_book.spread is not None and yes_book.spread > config.max_spread:
        return None
    if no_book.spread is not None and no_book.spread > config.max_spread:
        return None

    # Size is capped by the thinnest visible side, then by the target.
    visible = min(
        sum((lv.size for lv in yes_book.asks), ZERO),
        sum((lv.size for lv in no_book.asks), ZERO),
    )
    if visible < config.min_visible_size:
        return None
    size = min(config.target_size, visible)
    if size <= ZERO:
        return None

    yes_avg = _avg_buy_price(yes_book, size)
    no_avg = _avg_buy_price(no_book, size)
    if yes_avg is None or no_avg is None:
        return None

    gross = yes_avg + no_avg
    net = gross + fee_per_share(yes_avg, config.fee_rate) + fee_per_share(no_avg, config.fee_rate)

    if net > config.max_basket_cost:
        return None
    edge_per_share = ONE - net
    if edge_per_share < config.min_edge:
        return None

    return ArbIntent(
        market=market,
        legs=(
            ArbLeg(token_id=market.yes_token_id, side="BUY", price=yes_avg, size=size),
            ArbLeg(token_id=market.no_token_id, side="BUY", price=no_avg, size=size),
        ),
        size=size,
        gross_cost=gross,
        net_cost=net,
        edge_per_share=edge_per_share,
        edge_usd=edge_per_share * size,
    )
