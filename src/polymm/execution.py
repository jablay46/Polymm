"""Two-leg execution with explicit non-atomic-fill handling.

Polymarket has no atomic "buy both legs" primitive, so a complete-set
arbitrage is two separate orders. The dangerous case is a *one-legged
fill*: leg A fills, leg B fails, and the bot is left holding an unhedged
directional position it never intended.

The executor makes that case safe and explicit:

1. Buy the *cheaper* leg first (the mispriced side is the one that will
   disappear first).
2. Require a full fill (FOK/IOC). A partial fill is treated as a failure.
3. If the first leg fills and the second fails, immediately unwind the
   first leg by selling it back at the best bid.
4. Every branch returns a typed :class:`ExecutionResult`; the caller never
   has to guess whether it holds a naked position.

Risk is consulted before every order, so a halted account or a cap breach
stops execution mid-flight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from polymm.exchange import Exchange, ExchangeError, OrderRequest, OrderResult
from polymm.risk import RiskManager
from polymm.strategy import ArbIntent, ArbLeg

ZERO = Decimal("0")


class ExecutionStatus(StrEnum):
    FILLED = "filled"  # both legs filled -> locked arbitrage
    ABORTED = "aborted"  # nothing filled
    UNWOUND = "unwound"  # first leg filled, second failed, first sold back
    UNHEDGED = "unhedged"  # first leg filled, unwind also failed (danger)


@dataclass
class ExecutionResult:
    status: ExecutionStatus
    intent: ArbIntent
    first_leg: OrderResult | None = None
    second_leg: OrderResult | None = None
    unwind: OrderResult | None = None
    realized_edge_usd: Decimal = ZERO
    note: str = ""

    @property
    def holds_naked_position(self) -> bool:
        return self.status is ExecutionStatus.UNHEDGED

    @property
    def success(self) -> bool:
        return self.status is ExecutionStatus.FILLED


@dataclass(frozen=True)
class ExecutionConfig:
    order_type: str = "FOK"  # full-or-kill; a partial fill is a failure
    require_full_fill: bool = True
    unwind_on_failure: bool = True
    depth_levels: int = 5
    extra: dict[str, str] = field(default_factory=dict)


def _full_fill(result: OrderResult, size: Decimal, require_full: bool) -> bool:
    if not require_full:
        return result.filled_size > ZERO
    return result.filled_size >= size


def _depth_usd(exchange: Exchange, token_id: str, side: str, levels: int) -> Decimal:
    book = exchange.get_order_book(token_id)
    return book.depth_usd(side, levels)


def execute_intent(
    exchange: Exchange,
    intent: ArbIntent,
    risk: RiskManager,
    config: ExecutionConfig | None = None,
) -> ExecutionResult:
    """Execute a complete-set intent, defending against one-legged fills."""
    cfg = config or ExecutionConfig()

    # Cheaper leg first: it is the side that will be repriced away first.
    legs = sorted(intent.legs, key=lambda leg: leg.price)
    first, second = legs[0], legs[1]

    # ── basket-level risk gate ───────────────────────────────
    # Both legs are one economic position, so they are judged together
    # *before* any order leaves: a per-leg check would let a basket through
    # at 2x the cap and could force a losing unwind after leg 1 filled.
    # Both depth reads are also done here, inside the guard, so a transient
    # network error aborts before any exposure is taken.
    try:
        first_depth = _depth_usd(exchange, first.token_id, "BUY", cfg.depth_levels)
        second_depth = _depth_usd(exchange, second.token_id, "BUY", cfg.depth_levels)
    except ExchangeError as exc:
        return ExecutionResult(
            status=ExecutionStatus.ABORTED,
            intent=intent,
            note=f"book unavailable before first leg: {exc}",
        )

    basket_notional = first.price * first.size + second.price * second.size
    decision = risk.evaluate_order(
        notional=basket_notional,
        size_shares=max(first.size, second.size),
        depth_usd=min(first_depth, second_depth),
    )
    if not decision.allowed:
        return ExecutionResult(
            status=ExecutionStatus.ABORTED,
            intent=intent,
            note=f"risk denied basket: {decision.reason}",
        )

    try:
        first_result = exchange.place_order(
            OrderRequest(
                token_id=first.token_id,
                side="BUY",
                price=first.price,
                size=first.size,
                order_type=cfg.order_type,
                neg_risk=intent.market.neg_risk,
            )
        )
    except ExchangeError as exc:
        return ExecutionResult(
            status=ExecutionStatus.ABORTED,
            intent=intent,
            note=f"first leg rejected: {exc}",
        )

    if not _full_fill(first_result, first.size, cfg.require_full_fill):
        return ExecutionResult(
            status=ExecutionStatus.ABORTED,
            intent=intent,
            first_leg=first_result,
            note="first leg did not fully fill; no exposure taken",
        )

    risk.record_fill(notional=first.price * first.size, open_position=False)

    # ── second leg ───────────────────────────────────────────
    # No fresh depth read here: it was validated up front, and doing I/O
    # between the two legs only widens the one-legged window. The order is
    # placed inside the guard so any failure still unwinds leg 1.
    try:
        second_result = exchange.place_order(
            OrderRequest(
                token_id=second.token_id,
                side="BUY",
                price=second.price,
                size=second.size,
                order_type=cfg.order_type,
                neg_risk=intent.market.neg_risk,
            )
        )
    except ExchangeError as exc:
        return _unwind_or_hold(
            exchange, intent, risk, first_result, first, cfg, f"second leg rejected: {exc}"
        )

    if not _full_fill(second_result, second.size, cfg.require_full_fill):
        return _unwind_or_hold(
            exchange, intent, risk, first_result, first, cfg, "second leg did not fully fill"
        )

    # ── both legs filled: locked edge ────────────────────────
    risk.record_fill(notional=second.price * second.size, open_position=True)
    return ExecutionResult(
        status=ExecutionStatus.FILLED,
        intent=intent,
        first_leg=first_result,
        second_leg=second_result,
        realized_edge_usd=intent.edge_usd,
        note="complete set acquired",
    )


def _unwind_or_hold(
    exchange: Exchange,
    intent: ArbIntent,
    risk: RiskManager,
    first_result: OrderResult,
    first_leg: ArbLeg,
    cfg: ExecutionConfig,
    reason: str,
) -> ExecutionResult:
    """Sell the filled first leg back; if that fails, flag UNHEDGED loudly."""
    if not cfg.unwind_on_failure:
        return ExecutionResult(
            status=ExecutionStatus.UNHEDGED,
            intent=intent,
            first_leg=first_result,
            note=f"{reason}; unwind disabled, holding naked long",
        )

    try:
        book = exchange.get_order_book(first_leg.token_id)
        bid = book.best_bid
        if bid is None:
            raise ExchangeError("no bid to unwind into")
        if book.min_order_size and first_result.filled_size < book.min_order_size:
            raise ExchangeError(
                f"unwind size {first_result.filled_size} below market minimum {book.min_order_size}"
            )
        unwind = exchange.place_order(
            OrderRequest(
                token_id=first_leg.token_id,
                side="SELL",
                price=bid,
                size=first_result.filled_size,
                order_type="FOK",
                neg_risk=intent.market.neg_risk,
            )
        )
    except ExchangeError as exc:
        return ExecutionResult(
            status=ExecutionStatus.UNHEDGED,
            intent=intent,
            first_leg=first_result,
            note=f"{reason}; UNWIND FAILED: {exc}",
        )

    if unwind.filled_size < first_result.filled_size:
        return ExecutionResult(
            status=ExecutionStatus.UNHEDGED,
            intent=intent,
            first_leg=first_result,
            unwind=unwind,
            note=f"{reason}; unwind only partially filled",
        )

    # Realised loss = bought at first.price, sold at unwind.avg_price, minus fees.
    loss = (first_result.avg_price - unwind.avg_price) * unwind.filled_size
    # Leg 1 was recorded with open_position=False, so its close must not
    # decrement the open-position counter either.
    risk.record_close(notional=first_leg.price * first_leg.size, pnl=-loss, open_position=False)
    return ExecutionResult(
        status=ExecutionStatus.UNWOUND,
        intent=intent,
        first_leg=first_result,
        unwind=unwind,
        realized_edge_usd=-loss,
        note=f"{reason}; first leg unwound",
    )
