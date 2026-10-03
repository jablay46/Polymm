"""Decode Polymarket CTF Exchange ``OrderFilled`` logs.

The decoder turns a raw log (topics + data) into a :class:`Trade`, which is
the only shape the rest of the bot consumes. It is deliberately pure: no
web3, no network. Tests feed synthetic logs, including malformed ones.

Layout of the event (see the exchange ABI):

    OrderFilled(
        bytes32 indexed orderHash,
        address indexed maker,
        address indexed taker,
        uint256 makerAssetId,
        uint256 takerAssetId,
        uint256 makerAmountFilled,
        uint256 takerAmountFilled,
        uint256 makerFee,
        uint256 takerFee,
        uint256 protocolFee,
    )

There are **three** indexed parameters, so a log carries four topics:
``[signature, orderHash, maker, taker]``. On Polymarket, USDC has asset id
``0``; an outcome share is a large ERC-1155 token id. Exactly one side of a
fill is USDC, so the non-zero id identifies the traded outcome token, and
the USDC leg's amount is the notional.
"""

from __future__ import annotations

from dataclasses import dataclass

# keccak256 of the canonical signature:
#   OrderFilled(bytes32,address,address,uint256,uint256,uint256,
#               uint256,uint256,uint256,uint256)
ORDER_FILLED_TOPIC = "0x91105c381b63850378a56c45fe0396c97f8cc7bcd12b48dc0bb8b2b47271706d"

USDC_ASSET_ID = 0
USDC_DECIMALS = 6
USDC_SCALE = 10**USDC_DECIMALS

# Non-indexed data fields, in order.
_DATA_FIELDS = (
    "makerAssetId",
    "takerAssetId",
    "makerAmountFilled",
    "takerAmountFilled",
    "makerFee",
    "takerFee",
    "protocolFee",
)


class DecodeError(ValueError):
    """Raised when a log cannot be decoded into a trade."""


@dataclass(frozen=True)
class Trade:
    """A normalised fill.

    ``side`` is the *taker's* direction: BUY means the taker bought outcome
    shares with USDC. ``price`` is USDC per share; ``size`` is shares.
    """

    order_hash: str
    maker: str
    taker: str
    token_id: str
    side: str
    price: float
    size: float
    usdc_amount: float
    maker_fee: int
    taker_fee: int
    protocol_fee: int

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"}:
            raise DecodeError(f"side must be BUY or SELL, got {self.side!r}")
        if self.size <= 0:
            raise DecodeError(f"size must be positive, got {self.size}")


def _strip_hex(value: str) -> str:
    return value[2:] if value.startswith("0x") else value


def _decode_topic_uint(topic: str) -> int:
    body = _strip_hex(topic)
    if len(body) != 64:
        raise DecodeError(f"topic must be 32 bytes, got {len(body) // 2}")
    try:
        return int(body, 16)
    except ValueError as exc:
        raise DecodeError(f"non-hex topic: {topic!r}") from exc


def _decode_topic_address(topic: str) -> str:
    body = _strip_hex(topic)
    if len(body) != 64:
        raise DecodeError(f"topic must be 32 bytes, got {len(body) // 2}")
    return "0x" + body[24:].lower()


def decode_order_filled(
    *,
    topics: list[str],
    data: str,
    order_hash: str | None = None,
) -> Trade:
    """Decode a single ``OrderFilled`` log into a :class:`Trade`.

    Args:
        topics: Log topics: ``[signature, orderHash, maker, taker]``.
        data: Hex-encoded non-indexed data (7 uint256 words).
        order_hash: Optional explicit order hash; when omitted ``topics[1]``
            is used (orderHash is indexed).

    Raises:
        DecodeError: on malformed input or when neither leg is a share.
    """
    if len(topics) < 4:
        raise DecodeError(f"expected 4 topics, got {len(topics)}")

    resolved_order_hash = order_hash if order_hash is not None else topics[1]
    maker = _decode_topic_address(topics[2])
    taker = _decode_topic_address(topics[3])

    body = _strip_hex(data)
    if len(body) % 64 != 0:
        raise DecodeError(f"data length {len(body)} is not a multiple of 64 hex chars")
    if len(body) < 64 * len(_DATA_FIELDS):
        raise DecodeError(f"data has {len(body) // 64} words, need {len(_DATA_FIELDS)}")

    words: dict[str, int] = {}
    for i, name in enumerate(_DATA_FIELDS):
        chunk = body[i * 64 : (i + 1) * 64]
        try:
            words[name] = int(chunk, 16)
        except ValueError as exc:
            raise DecodeError(f"non-hex data word for {name}: {chunk!r}") from exc

    maker_asset_id = words["makerAssetId"]
    taker_asset_id = words["takerAssetId"]

    if maker_asset_id != USDC_ASSET_ID:
        # Maker sold shares; taker paid USDC.
        token_id = maker_asset_id
        size = words["makerAmountFilled"] / USDC_SCALE
        usdc_amount = words["takerAmountFilled"] / USDC_SCALE
        side = "BUY"
    elif taker_asset_id != USDC_ASSET_ID:
        # Maker paid USDC; taker sold shares.
        token_id = taker_asset_id
        size = words["takerAmountFilled"] / USDC_SCALE
        usdc_amount = words["makerAmountFilled"] / USDC_SCALE
        side = "SELL"
    else:
        raise DecodeError("both asset ids are USDC; not a share fill")

    price = usdc_amount / size if size > 0 else 0.0

    return Trade(
        order_hash=resolved_order_hash,
        maker=maker,
        taker=taker,
        token_id=str(token_id),
        side=side,
        price=price,
        size=size,
        usdc_amount=usdc_amount,
        maker_fee=words["makerFee"],
        taker_fee=words["takerFee"],
        protocol_fee=words["protocolFee"],
    )


def is_order_filled(topics: list[str]) -> bool:
    """True when the log's signature topic matches OrderFilled."""
    return bool(topics) and topics[0].lower() == ORDER_FILLED_TOPIC
