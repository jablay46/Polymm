"""Tests for the OrderFilled log decoder (task 0.5.5).

Logs are built by hand so every field is exact and no network is needed.
The two cases that matter are the ones that trip naive decoders: the USDC
leg being the *taker* side (a maker selling shares), and a share id so large
it must stay a string.
"""

from __future__ import annotations

import pytest

from polymm.data.decoder import (
    ORDER_FILLED_TOPIC,
    DecodeError,
    decode_order_filled,
    is_order_filled,
)

pytestmark = pytest.mark.unit

MAKER = "0x1111111111111111111111111111111111111111"
TAKER = "0x2222222222222222222222222222222222222222"
ORDER_HASH = "0x" + "ab" * 32
BIG_TOKEN_ID = 10**40  # deliberately huge ERC-1155 id


def _topic_address(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


def _word(value: int) -> str:
    return f"{value:064x}"


def _data(*values: int) -> str:
    return "0x" + "".join(_word(v) for v in values)


def _topics() -> list[str]:
    return [
        ORDER_FILLED_TOPIC,
        ORDER_HASH,
        _topic_address(MAKER),
        _topic_address(TAKER),
    ]


# ── taker buys shares (maker sells) ───────────────────────────


def test_maker_sells_shares_taker_buys() -> None:
    # maker gives 10 shares (asset id != 0), taker pays 4 USDC.
    trade = decode_order_filled(
        topics=_topics(),
        data=_data(BIG_TOKEN_ID, 0, 10_000_000, 4_000_000, 0, 0, 0),
    )
    assert trade.side == "BUY"  # taker bought
    assert trade.token_id == str(BIG_TOKEN_ID)
    assert trade.size == pytest.approx(10.0)
    assert trade.usdc_amount == pytest.approx(4.0)
    assert trade.price == pytest.approx(0.4)
    assert trade.maker == MAKER
    assert trade.taker == TAKER
    assert trade.order_hash == ORDER_HASH


# ── taker sells shares (maker buys) ───────────────────────────


def test_maker_buys_shares_taker_sells() -> None:
    # maker pays 6 USDC, taker gives 10 shares.
    trade = decode_order_filled(
        topics=_topics(),
        data=_data(0, BIG_TOKEN_ID, 6_000_000, 10_000_000, 0, 0, 0),
    )
    assert trade.side == "SELL"  # taker sold
    assert trade.token_id == str(BIG_TOKEN_ID)
    assert trade.size == pytest.approx(10.0)
    assert trade.usdc_amount == pytest.approx(6.0)
    assert trade.price == pytest.approx(0.6)


# ── fees and order hash ───────────────────────────────────────


def test_fees_are_preserved() -> None:
    trade = decode_order_filled(
        topics=_topics(),
        data=_data(BIG_TOKEN_ID, 0, 10_000_000, 4_000_000, 111, 222, 333),
    )
    assert (trade.maker_fee, trade.taker_fee, trade.protocol_fee) == (111, 222, 333)


def test_explicit_order_hash_overrides_topic() -> None:
    trade = decode_order_filled(
        topics=_topics(),
        data=_data(BIG_TOKEN_ID, 0, 10_000_000, 4_000_000, 0, 0, 0),
        order_hash="0x" + "cd" * 32,
    )
    assert trade.order_hash == "0x" + "cd" * 32


def test_default_order_hash_is_topics_one() -> None:
    trade = decode_order_filled(
        topics=_topics(),
        data=_data(BIG_TOKEN_ID, 0, 10_000_000, 4_000_000, 0, 0, 0),
    )
    assert trade.order_hash == ORDER_HASH


# ── precision: no float drift on a huge token id ──────────────


def test_huge_token_id_kept_as_string() -> None:
    trade = decode_order_filled(
        topics=_topics(),
        data=_data(BIG_TOKEN_ID, 0, 1_000_000, 500_000, 0, 0, 0),
    )
    assert trade.token_id == str(BIG_TOKEN_ID)
    assert int(trade.token_id) == BIG_TOKEN_ID


# ── malformed input fails closed ──────────────────────────────


def test_too_few_topics_raises() -> None:
    with pytest.raises(DecodeError, match="expected 4 topics"):
        decode_order_filled(topics=[ORDER_FILLED_TOPIC], data=_data(*([0] * 7)))


def test_short_data_raises() -> None:
    with pytest.raises(DecodeError, match="words, need"):
        decode_order_filled(topics=_topics(), data=_data(1, 2, 3))


def test_odd_length_data_raises() -> None:
    with pytest.raises(DecodeError, match="multiple of 64"):
        decode_order_filled(topics=_topics(), data="0xabc")


def test_both_usdc_raises() -> None:
    with pytest.raises(DecodeError, match="both asset ids are USDC"):
        decode_order_filled(topics=_topics(), data=_data(0, 0, 1, 1, 0, 0, 0))


def test_bad_address_topic_length_raises() -> None:
    with pytest.raises(DecodeError, match="32 bytes"):
        decode_order_filled(
            topics=[ORDER_FILLED_TOPIC, ORDER_HASH, "0x1234", _topic_address(TAKER)],
            data=_data(BIG_TOKEN_ID, 0, 1_000_000, 500_000, 0, 0, 0),
        )


def test_zero_size_raises() -> None:
    with pytest.raises(DecodeError, match="size must be positive"):
        decode_order_filled(topics=_topics(), data=_data(BIG_TOKEN_ID, 0, 0, 0, 0, 0, 0))


# ── topic filter ──────────────────────────────────────────────


def test_is_order_filled() -> None:
    assert is_order_filled([ORDER_FILLED_TOPIC]) is True
    assert is_order_filled([ORDER_FILLED_TOPIC.upper().replace("0X", "0x")]) is True
    assert is_order_filled(["0x" + "00" * 32]) is False
    assert is_order_filled([]) is False


def test_topic_hash_matches_signature() -> None:
    """Guard against a typo in the hardcoded topic constant."""
    eth_utils = pytest.importorskip("eth_utils")

    signature = (
        "OrderFilled(bytes32,address,address,uint256,uint256,"
        "uint256,uint256,uint256,uint256,uint256)"
    )
    assert "0x" + eth_utils.keccak(text=signature).hex() == ORDER_FILLED_TOPIC


def test_side_validation_rejects_junk() -> None:
    from polymm.data.decoder import Trade

    with pytest.raises(DecodeError, match="side must be"):
        Trade(
            order_hash="0x0",
            maker=MAKER,
            taker=TAKER,
            token_id="1",
            side="SIDEWAYS",
            price=0.5,
            size=1.0,
            usdc_amount=0.5,
            maker_fee=0,
            taker_fee=0,
            protocol_fee=0,
        )
