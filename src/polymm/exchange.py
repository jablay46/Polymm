"""Exchange abstraction and the live CLOB adapter.

The bot only ever talks to an :class:`Exchange`. That keeps the strategy and
risk layers identical in paper trading and live trading, and means the live
adapter is the *only* module that touches the network or the API keys.

``ClobExchange`` wraps the official ``py_clob_client``. It is deliberately
thin: it converts our Decimal prices to the client's expected form at the
boundary, and converts the response back into typed results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from polymm.market import OrderBook
from polymm.pricing import tick_round, validate_price

ZERO = Decimal("0")


class ExchangeError(RuntimeError):
    """Any failure talking to the exchange."""


@dataclass(frozen=True)
class OrderRequest:
    token_id: str
    side: str  # BUY / SELL
    price: Decimal
    size: Decimal
    order_type: str = "GTC"  # GTC / FOK / IOC
    neg_risk: bool = False

    def __post_init__(self) -> None:
        if self.side.upper() not in {"BUY", "SELL"}:
            raise ExchangeError(f"side must be BUY or SELL, got {self.side!r}")
        if self.size <= ZERO:
            raise ExchangeError(f"size must be positive, got {self.size}")


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    status: str
    filled_size: Decimal = ZERO
    avg_price: Decimal = ZERO
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_filled(self) -> bool:
        return self.filled_size > ZERO


@runtime_checkable
class Exchange(Protocol):
    """Everything the bot needs from a venue."""

    def get_order_book(self, token_id: str) -> OrderBook: ...

    def place_order(self, request: OrderRequest) -> OrderResult: ...

    def cancel(self, order_id: str) -> bool: ...

    def cancel_all(self) -> None: ...

    def balance_usdc(self) -> Decimal: ...


class ClobExchange:
    """Live adapter over ``py_clob_client``.

    The client is injected so tests can pass a fake; nothing here opens a
    socket at construction time.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def get_order_book(self, token_id: str) -> OrderBook:
        try:
            summary = self._client.get_order_book(token_id)
        except Exception as exc:  # noqa: BLE001 - normalise transport errors
            raise ExchangeError(f"get_order_book failed for {token_id}: {exc}") from exc
        return OrderBook.from_clob(summary)

    def place_order(self, request: OrderRequest) -> OrderResult:
        from py_clob_client.clob_types import (  # type: ignore[import-untyped]
            OrderArgs,
            OrderType,
            PartialCreateOrderOptions,
        )
        from py_clob_client.order_builder.constants import (  # type: ignore[import-untyped]
            BUY,
            SELL,
        )

        book = self.get_order_book(request.token_id)
        # Snap to the market's tick so the client never rejects the price.
        price = tick_round(request.price, book.tick_size, mode="down")
        validate_price(price, name="order price")

        side = BUY if request.side.upper() == "BUY" else SELL
        args = OrderArgs(
            token_id=request.token_id,
            price=float(price),
            size=float(request.size),
            side=side,
        )
        options = PartialCreateOrderOptions(
            tick_size=str(book.tick_size),
            neg_risk=request.neg_risk,
        )
        try:
            signed = self._client.create_order(args, options)
            response = self._client.post_order(signed, OrderType[request.order_type])
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"place_order failed: {exc}") from exc

        return OrderResult(
            order_id=str(response.get("orderID", response.get("orderId", ""))),
            status=str(response.get("status", "unknown")),
            raw=dict(response),
        )

    def cancel(self, order_id: str) -> bool:
        try:
            self._client.cancel(order_id)
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"cancel failed for {order_id}: {exc}") from exc
        return True

    def cancel_all(self) -> None:
        try:
            self._client.cancel_all()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"cancel_all failed: {exc}") from exc

    def balance_usdc(self) -> Decimal:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        try:
            resp = self._client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"balance fetch failed: {exc}") from exc
        # The API returns balance in 6-decimal USDC units.
        raw = resp.get("balance", "0")
        return Decimal(str(raw)) / Decimal(10**6)
