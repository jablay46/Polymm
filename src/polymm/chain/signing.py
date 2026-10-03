"""EIP-712 order signing and L1/L2 authentication headers.

Behaviour mirrors the official ``py-clob-client``:

* ``signing/hmac.py`` — HMAC over ``timestamp + method + path + body`` keyed
  by the **base64-decoded** api_secret, returned as base64url.
* ``signing/eip712.py`` — ClobAuth message signed against the ``ClobAuthDomain``
  with chain id.
* ``order_builder`` — EIP-712 ``Order`` struct; ``side``/``signatureType`` are
  ``uint8``; maker/taker amounts are scaled to 6 decimals.

The crypto primitives are imported lazily so tier-1 unit tests can exercise
the pure helpers (rounding, message construction) without a crypto stack.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

from polymm.chain.constants import (
    CLOB_AUTH_MESSAGE,
    CLOB_DOMAIN_NAME,
    CLOB_DOMAIN_VERSION,
    EXCHANGE_DOMAIN_NAME,
    EXCHANGE_DOMAIN_VERSION,
    ORDER_TYPE_STRING,
    SIDE_BUY,
    SIDE_SELL,
    SIG_TYPE_EOA,
    ZERO_ADDRESS,
    order_type_hash,
)

# USDC and CTF outcome shares are both 6-decimal on Polymarket.
TOKEN_DECIMALS = 6

# Per-tick rounding: (price, size, amount) decimal places.
ROUNDING: dict[str, tuple[int, int, int]] = {
    "0.1": (1, 2, 3),
    "0.01": (2, 2, 4),
    "0.001": (3, 2, 5),
    "0.0001": (4, 2, 6),
}


# ── pure helpers (no crypto needed) ───────────────────────────


def _scale(digits: int) -> float:
    # ``10 ** digits`` is typed as Any by mypy (it may be float for negative
    # exponents); pinning it to float keeps the rounding helpers well-typed.
    return float(10**digits)


def round_down(x: float, digits: int) -> float:
    from math import floor

    return floor(x * _scale(digits)) / _scale(digits)


def round_up(x: float, digits: int) -> float:
    from math import ceil

    return ceil(x * _scale(digits)) / _scale(digits)


def round_normal(x: float, digits: int) -> float:
    return round(x * _scale(digits)) / _scale(digits)


def to_token_decimals(x: float) -> int:
    """Scale a human amount to 6-decimal integer units."""
    f = (10**TOKEN_DECIMALS) * x
    return int(round_normal(f, 0))


def build_hmac_message(timestamp: str, method: str, path: str, body: str | None) -> str:
    """Canonical HMAC payload: ``timestamp + METHOD + path [+ body]``.

    The body's single quotes are replaced with double quotes so the message
    matches the Go/TypeScript clients (see py-clob-client ``signing/hmac.py``).
    """
    message = f"{timestamp}{method}{path}"
    if body:
        message += str(body).replace("'", '"')
    return message


def build_hmac_signature(
    secret: str, timestamp: str, method: str, path: str, body: str | None = None
) -> str:
    """HMAC-SHA256 over the canonical payload, keyed by base64-decoded secret."""
    base64_secret = base64.urlsafe_b64decode(secret)
    message = build_hmac_message(timestamp, method, path, body)
    digest = hmac.new(base64_secret, message.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8")


def order_amounts(side: int, size: float, price: float, tick_size: str) -> tuple[int, int]:
    """Return ``(maker_amount, taker_amount)`` in 6-decimal units.

    BUY: the maker pays USDC (maker amount) and receives shares (taker amount).
    SELL: the maker gives shares (maker amount) and receives USDC (taker amount).

    Mirrors ``OrderBuilder.get_order_amounts`` including its rounding dance
    so the amounts match what the exchange expects exactly.
    """
    price_dp, size_dp, amount_dp = ROUNDING[tick_size]
    raw_price = round_normal(price, price_dp)

    if side == SIDE_BUY:
        raw_taker = round_down(size, size_dp)
        raw_maker = raw_taker * raw_price
        raw_maker = _cap_decimals(raw_maker, amount_dp)
        return to_token_decimals(raw_maker), to_token_decimals(raw_taker)

    if side == SIDE_SELL:
        raw_maker = round_down(size, size_dp)
        raw_taker = raw_maker * raw_price
        raw_taker = _cap_decimals(raw_taker, amount_dp)
        return to_token_decimals(raw_maker), to_token_decimals(raw_taker)

    raise ValueError(f"side must be BUY ({SIDE_BUY}) or SELL ({SIDE_SELL}), got {side}")


def _cap_decimals(value: float, amount_dp: int) -> float:
    if _decimal_places(value) > amount_dp:
        value = round_up(value, amount_dp + 4)
        if _decimal_places(value) > amount_dp:
            value = round_down(value, amount_dp)
    return value


def _decimal_places(x: float) -> int:
    from decimal import Decimal

    exponent = Decimal(str(x)).as_tuple().exponent
    return abs(int(exponent))


# ── EIP-712 order ─────────────────────────────────────────────


@dataclass(frozen=True)
class OrderInputs:
    token_id: str
    side: int
    size: float
    price: float
    tick_size: str
    fee_rate_bps: int = 0
    expiration: int = 0
    nonce: int = 0


def build_order_struct(
    *,
    wallet_address: str,
    funder_address: str,
    signature_type: int,
    inputs: OrderInputs,
    salt: int,
) -> dict[str, Any]:
    """Assemble the EIP-712 ``Order`` message (field order is significant)."""
    maker_amount, taker_amount = order_amounts(
        inputs.side, inputs.size, inputs.price, inputs.tick_size
    )
    return {
        "salt": salt,
        "maker": funder_address,
        "signer": wallet_address,
        "taker": ZERO_ADDRESS,
        "tokenId": int(inputs.token_id),
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "expiration": inputs.expiration,
        "nonce": inputs.nonce,
        "feeRateBps": inputs.fee_rate_bps,
        "side": inputs.side,
        "signatureType": signature_type,
    }


def _require_eth_account() -> Any:
    try:
        from eth_account import Account
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "order signing needs the 'chain' extra: uv pip install -e '.[chain]'"
        ) from exc
    return Account


def address_from_key(private_key: str) -> str:
    """Derive the checksummed address for a private key."""
    Account = _require_eth_account()
    return str(Account.from_key(private_key).address)


def _eip712_domain(chain_id: int, verifying_contract: str) -> dict[str, Any]:
    return {
        "name": EXCHANGE_DOMAIN_NAME,
        "version": EXCHANGE_DOMAIN_VERSION,
        "chainId": chain_id,
        "verifyingContract": verifying_contract,
    }


def sign_order(
    *,
    order: dict[str, Any],
    private_key: str,
    chain_id: int,
    verifying_contract: str,
) -> str:
    """Sign an EIP-712 order and return the 0x-prefixed signature."""
    from eth_account.messages import encode_typed_data

    Account = _require_eth_account()
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Order": _order_types(),
        },
        "primaryType": "Order",
        "domain": _eip712_domain(chain_id, verifying_contract),
        "message": order,
    }
    encoded = encode_typed_data(full_message=typed)
    signed = Account.sign_message(encoded, private_key=private_key)
    sig = bytes(signed.signature).hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _order_types() -> list[dict[str, str]]:
    types: list[dict[str, str]] = []
    for field in ORDER_TYPE_STRING[len("Order(") : -1].split(","):
        type_name, name = field.split(" ")
        types.append({"name": name, "type": type_name})
    return types


def hash_order(*, order: dict[str, Any], chain_id: int, verifying_contract: str) -> str:
    """EIP-712 struct hash of an order (hex, 0x-prefixed)."""
    from eth_utils import keccak
    from poly_eip712_structs import (
        Address,
        EIP712Struct,
        Uint,
    )

    class _Order(EIP712Struct):  # type: ignore[misc]  # pragma: no cover - lib adapter
        salt = Uint(256)
        maker = Address()
        signer = Address()
        taker = Address()
        tokenId = Uint(256)
        makerAmount = Uint(256)
        takerAmount = Uint(256)
        expiration = Uint(256)
        nonce = Uint(256)
        feeRateBps = Uint(256)
        side = Uint(8)
        signatureType = Uint(8)

    domain = {
        "name": EXCHANGE_DOMAIN_NAME,
        "version": EXCHANGE_DOMAIN_VERSION,
        "chainId": chain_id,
        "verifyingContract": verifying_contract,
    }
    struct = _Order(**order)
    digest = bytes(keccak(struct.signable_bytes(domain)))
    return "0x" + digest.hex()


def sign_clob_auth(private_key: str, chain_id: int, timestamp: int, nonce: int = 0) -> str:
    """Sign the ClobAuth message used for L1 API-key creation."""
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    address = Account.from_key(private_key).address
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ],
        },
        "primaryType": "ClobAuth",
        "domain": {
            "name": CLOB_DOMAIN_NAME,
            "version": CLOB_DOMAIN_VERSION,
            "chainId": chain_id,
        },
        "message": {
            "address": address,
            "timestamp": str(timestamp),
            "nonce": nonce,
            "message": CLOB_AUTH_MESSAGE,
        },
    }
    signed = Account.sign_message(encode_typed_data(full_message=typed), private_key=private_key)
    sig = bytes(signed.signature).hex()
    return sig if sig.startswith("0x") else "0x" + sig


def l2_headers(
    *,
    address: str,
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    method: str,
    path: str,
    body: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Build the L2 HMAC auth headers Polymarket expects."""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    signature = build_hmac_signature(api_secret, ts, method, path, body)
    return {
        "POLY_ADDRESS": address,
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": ts,
        "POLY_API_KEY": api_key,
        "POLY_PASSPHRASE": api_passphrase,
    }


def l1_headers(*, address: str, signature: str, timestamp: int, nonce: int = 0) -> dict[str, str]:
    """Build the L1 auth headers (used to create/derive API keys)."""
    return {
        "POLY_ADDRESS": address,
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": str(timestamp),
        "POLY_NONCE": str(nonce),
    }


__all__ = [
    "ORDER_TYPE_STRING",
    "OrderInputs",
    "address_from_key",
    "build_hmac_message",
    "build_hmac_signature",
    "build_order_struct",
    "hash_order",
    "l1_headers",
    "l2_headers",
    "order_amounts",
    "order_type_hash",
    "round_down",
    "round_normal",
    "round_up",
    "sign_clob_auth",
    "sign_order",
    "to_token_decimals",
    "SIG_TYPE_EOA",
]
