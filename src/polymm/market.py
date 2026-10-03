"""Order-book model, decoupled from the exchange client.

The Polymarket feed is not guaranteed to deliver levels best-first, and a
bot that assumes ordering will size against the wrong end of the book. This
model parses the official ``OrderBookSummary`` shape and always sorts bids
descending and asks ascending, so :func:`walk_book` consumes the true best
prices first.

``neg_risk`` is carried through because it selects which exchange contract
signs the order (see :mod:`polymm.chain.constants`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from polymm.pricing import Level, PricingError


def _dec(value: object, *, name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - normalise to our error type
        raise PricingError(f"{name} is not a number: {value!r}") from exc


@dataclass(frozen=True)
class OrderBook:
    """A two-sided book with levels sorted best-first."""

    token_id: str
    bids: tuple[Level, ...]  # descending price
    asks: tuple[Level, ...]  # ascending price
    tick_size: Decimal
    min_order_size: Decimal
    neg_risk: bool = False
    timestamp: str | None = None

    @classmethod
    def from_clob(cls, summary: Any) -> OrderBook:
        """Build from the official ``OrderBookSummary`` (or an equivalent dict)."""

        def get(field: str, default: Any = None) -> Any:
            if isinstance(summary, dict):
                return summary.get(field, default)
            return getattr(summary, field, default)

        token_id = get("asset_id") or get("token_id") or ""
        raw_bids = get("bids") or []
        raw_asks = get("asks") or []

        def levels(raw: list[Any]) -> list[Level]:
            out: list[Level] = []
            for item in raw:
                price = item["price"] if isinstance(item, dict) else item.price
                size = item["size"] if isinstance(item, dict) else item.size
                out.append(
                    Level(
                        price=_dec(price, name="price"),
                        size=_dec(size, name="size"),
                    )
                )
            return out

        # Defensive sort: never trust feed ordering.
        bids = sorted(levels(raw_bids), key=lambda lv: lv.price, reverse=True)
        asks = sorted(levels(raw_asks), key=lambda lv: lv.price)

        tick_raw = get("tick_size")
        tick = _dec(tick_raw, name="tick_size") if tick_raw else Decimal("0.01")
        min_raw = get("min_order_size")
        min_size = _dec(min_raw, name="min_order_size") if min_raw else Decimal("0")

        return cls(
            token_id=str(token_id),
            bids=tuple(bids),
            asks=tuple(asks),
            tick_size=tick,
            min_order_size=min_size,
            neg_risk=bool(get("neg_risk", False)),
            timestamp=get("timestamp"),
        )

    # ── top of book ───────────────────────────────────────────

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def is_crossed(self) -> bool:
        """True when best bid >= best ask (a data error, not a trade)."""
        if self.best_bid is None or self.best_ask is None:
            return False
        return self.best_bid >= self.best_ask

    def levels_for(self, side: str) -> tuple[Level, ...]:
        """Levels a taker consumes: asks to BUY, bids to SELL."""
        upper = side.upper()
        if upper == "BUY":
            return self.asks
        if upper == "SELL":
            return self.bids
        raise PricingError(f"side must be BUY or SELL, got {side!r}")

    def depth_usd(self, side: str, levels: int) -> Decimal:
        """USD resting within ``levels`` of the side a taker consumes."""
        total = Decimal("0")
        for level in self.levels_for(side)[:levels]:
            total += level.price * level.size
        return total


@dataclass(frozen=True)
class Market:
    """A binary market: two complementary outcome tokens.

    One YES share plus one NO share always settles to exactly 1.00 USDC,
    which is the invariant the complete-set arbitrage relies on.
    """

    condition_id: str
    yes_token_id: str
    no_token_id: str
    question: str = ""
    neg_risk: bool = False

    def __post_init__(self) -> None:
        if not self.condition_id:
            raise PricingError("market needs a condition_id")
        if not self.yes_token_id or not self.no_token_id:
            raise PricingError("market needs both yes and no token ids")
        if self.yes_token_id == self.no_token_id:
            raise PricingError("yes and no token ids must differ")

    @property
    def token_ids(self) -> tuple[str, str]:
        return (self.yes_token_id, self.no_token_id)
