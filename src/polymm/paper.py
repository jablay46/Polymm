"""Paper-trading exchange: a faithful in-memory simulation.

This is the runnable testnet path that needs no keys and no network. It is
the default in tests and in ``mock_trading`` mode, and it must model the
things that actually cost money:

* orders fill against the *real* book by walking levels, so a marketable
  order gets the true volume-weighted average price, not the top of book;
* a limit order only fills when it crosses the book;
* taker fees are charged with the exchange formula;
* balance and positions are tracked in Decimal.

It is intentionally conservative: a limit order that does not cross does
not fill (there is no queue model), which understates rather than overstates
profits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from polymm.exchange import (
    ExchangeError,
    OrderRequest,
    OrderResult,
)
from polymm.market import OrderBook
from polymm.pricing import fee_per_share, walk_book

ZERO = Decimal("0")
USDC_SCALE = Decimal(10**6)


@dataclass
class _Position:
    size: Decimal = ZERO
    cost: Decimal = ZERO

    @property
    def avg_cost(self) -> Decimal:
        return self.cost / self.size if self.size > ZERO else ZERO


@dataclass
class PaperExchange:
    """In-memory venue backed by caller-supplied books."""

    balance: Decimal = Decimal("1000")
    fee_rate: Decimal = Decimal("0.02")
    books: dict[str, OrderBook] = field(default_factory=dict)
    positions: dict[str, _Position] = field(default_factory=dict)
    fills: list[OrderResult] = field(default_factory=list)
    _seq: int = 0

    def set_book(self, book: OrderBook) -> None:
        self.books[book.token_id] = book

    def get_order_book(self, token_id: str) -> OrderBook:
        book = self.books.get(token_id)
        if book is None:
            raise ExchangeError(f"no book for token {token_id}")
        return book

    def balance_usdc(self) -> Decimal:
        return self.balance

    def cancel(self, order_id: str) -> bool:  # noqa: ARG002 - nothing rests
        return True

    def cancel_all(self) -> None:
        return None

    def place_order(self, request: OrderRequest) -> OrderResult:
        book = self.get_order_book(request.token_id)
        side = request.side.upper()

        if book.min_order_size and request.size < book.min_order_size:
            raise ExchangeError(f"size {request.size} below market minimum {book.min_order_size}")

        levels = book.levels_for(side)
        if not levels:
            raise ExchangeError(f"book for {request.token_id} has no {side} liquidity")

        # A limit order only fills the part that crosses the book.
        if side == "BUY":
            crossing = [lv for lv in levels if lv.price <= request.price]
        else:
            crossing = [lv for lv in levels if lv.price >= request.price]

        if not crossing:
            self._seq += 1
            return OrderResult(
                order_id=f"paper-{self._seq}",
                status="resting",
                filled_size=ZERO,
                avg_price=ZERO,
            )

        walk = walk_book(crossing, request.size)
        if walk.filled <= ZERO:
            raise ExchangeError("order filled zero size")

        # FOK is all-or-nothing: if the book cannot fill the whole size, the
        # order is killed and nothing changes. Accepting the partial (the old
        # behaviour) left an unintended position and hid execution bugs.
        if request.order_type.upper() == "FOK" and walk.filled < request.size:
            self._seq += 1
            return OrderResult(
                order_id=f"paper-{self._seq}",
                status="killed",
                filled_size=ZERO,
                avg_price=ZERO,
            )

        fee = fee_per_share(walk.avg_price, self.fee_rate) * walk.filled

        if side == "BUY":
            total_cost = walk.notional + fee
            if total_cost > self.balance:
                raise ExchangeError(f"insufficient balance: need {total_cost}, have {self.balance}")
            self.balance -= total_cost
            pos = self.positions.setdefault(request.token_id, _Position())
            pos.size += walk.filled
            pos.cost += total_cost
        else:
            held = self.positions.get(request.token_id)
            if held is None or held.size < walk.filled:
                raise ExchangeError("insufficient shares to sell")
            held.size -= walk.filled
            proceeds = walk.notional - fee
            held.cost -= held.avg_cost * walk.filled
            self.balance += proceeds

        self._seq += 1
        result = OrderResult(
            order_id=f"paper-{self._seq}",
            status="filled",
            filled_size=walk.filled,
            avg_price=walk.avg_price,
        )
        self.fills.append(result)
        return result

    # ── simulation helpers ────────────────────────────────────

    def position_size(self, token_id: str) -> Decimal:
        pos = self.positions.get(token_id)
        return pos.size if pos else ZERO

    def position_cost(self, token_id: str) -> Decimal:
        pos = self.positions.get(token_id)
        return pos.cost if pos else ZERO
