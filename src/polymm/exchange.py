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


# py-clob-client's OrderType is a plain class whose attributes are strings
# (``OrderType.FOK == "FOK"``), *not* an enum. ``OrderType[name]`` therefore
# returns a GenericAlias and JSON-serialising it fails, so resolve by
# attribute lookup instead. Polymarket has no "IOC"; its immediate-or-cancel
# type is FAK, so map the documented alias across.
_ORDER_TYPE_ALIASES = {"IOC": "FAK"}
_SUPPORTED_ORDER_TYPES = ("GTC", "FOK", "GTD", "FAK")


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


def _resolve_order_type(order_type_cls: Any, name: str) -> Any:
    """Map a request order-type name to the client's value.

    Uses attribute lookup (``OrderType.FOK``), never subscription
    (``OrderType["FOK"]``), because the latter returns a GenericAlias that
    cannot be serialised and makes every order fail with a TypeError.
    """
    key = _ORDER_TYPE_ALIASES.get(name.upper(), name.upper())
    value = getattr(order_type_cls, key, None)
    if value is None:
        raise ExchangeError(f"unsupported order type {name!r}; supported: {_SUPPORTED_ORDER_TYPES}")
    return value


def _parse_order_response(response: Any) -> OrderResult:
    """Turn a CLOB post_order response into a typed result.

    The response is a dict whose ``status`` says whether the order was
    accepted, matched, or rejected. We read ``success``/``errorMsg`` and the
    matched ``makingAmount``/``takingAmount`` so the caller knows the real
    filled size instead of assuming zero.
    """
    if not isinstance(response, dict):
        raise ExchangeError(f"unexpected order response type: {type(response).__name__}")

    order_id = str(response.get("orderID") or response.get("orderId") or "")
    status = str(response.get("status", "unknown"))
    success = response.get("success")
    error_msg = response.get("errorMsg") or response.get("error")

    filled_size = ZERO
    avg_price = ZERO
    taking = _to_decimal(response.get("takingAmount"))
    making = _to_decimal(response.get("makingAmount"))
    # A matched BUY spends ``makingAmount`` (USDC) for ``takingAmount`` shares.
    if taking and taking > ZERO and making and making > ZERO:
        filled_size = taking
        avg_price = making / taking

    if success is False and filled_size == ZERO:
        raise ExchangeError(f"order rejected by CLOB: {error_msg or status}")

    return OrderResult(
        order_id=order_id,
        status=status,
        filled_size=filled_size,
        avg_price=avg_price,
        raw=dict(response),
    )


def _to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return ZERO
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - normalise to our error type
        raise ExchangeError(f"non-numeric amount in order response: {value!r}") from exc


class ClobExchange:
    """Live adapter over ``py_clob_client``.

    The client is injected so tests can pass a fake; nothing here opens a
    socket at construction time.

    ``live_allowed`` is the dual-flag gate. When it is false the adapter
    refuses to place any order — the gate lives here, at the last possible
    point before the network, so no caller can bypass it by forgetting to
    run preflight.
    """

    def __init__(self, client: Any, *, live_allowed: bool = False) -> None:
        self._client = client
        self._live_allowed = live_allowed

    def get_order_book(self, token_id: str) -> OrderBook:
        try:
            summary = self._client.get_order_book(token_id)
        except Exception as exc:  # noqa: BLE001 - normalise transport errors
            raise ExchangeError(f"get_order_book failed for {token_id}: {exc}") from exc
        return OrderBook.from_clob(summary)

    def place_order(self, request: OrderRequest) -> OrderResult:
        if not self._live_allowed:
            raise ExchangeError(
                "live trading gate is closed (enable_trading must be true and "
                "mock_trading false); refusing to place a real order"
            )

        from py_clob_client.clob_types import (
            OrderArgs,
            OrderType,
            PartialCreateOrderOptions,
        )
        from py_clob_client.order_builder.constants import (
            BUY,
            SELL,
        )

        book = self.get_order_book(request.token_id)
        # BUY limit is the worst (highest) price we accept, so round up;
        # rounding down could put the limit below the deepest level and stop
        # the order from fully filling.
        mode = "up" if request.side.upper() == "BUY" else "down"
        price = tick_round(request.price, book.tick_size, mode=mode)
        validate_price(price, name="order price")

        size = request.size
        if book.min_order_size and size < book.min_order_size:
            raise ExchangeError(
                f"size {size} below market minimum {book.min_order_size} "
                f"for token {request.token_id}"
            )

        side = BUY if request.side.upper() == "BUY" else SELL
        args = OrderArgs(
            token_id=request.token_id,
            price=float(price),
            size=float(size),
            side=side,
        )
        options = PartialCreateOrderOptions(
            tick_size=str(book.tick_size),
            neg_risk=request.neg_risk,
        )
        order_type = _resolve_order_type(OrderType, request.order_type)
        try:
            signed = self._client.create_order(args, options)
            response = self._client.post_order(signed, order_type)
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"place_order failed: {exc}") from exc

        return _parse_order_response(response)

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
