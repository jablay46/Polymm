"""Tests for EIP-712 order signing and L1/L2 auth (tasks 0.5.4, 0.5.5-adjacent).

Values are cross-checked against the official ``py-clob-client`` where
possible: rounding behaviour, the EIP-712 type hash, and the HMAC payload.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmaclib

import pytest

from polymm.chain.constants import (
    CHAIN_AMOY,
    CHAIN_POLYGON,
    SIDE_BUY,
    SIDE_SELL,
    SIG_TYPE_EOA,
    get_contract_config,
    order_type_hash,
)
from polymm.chain.signing import (
    ORDER_TYPE_STRING,
    OrderInputs,
    build_hmac_message,
    build_hmac_signature,
    build_order_struct,
    l2_headers,
    order_amounts,
    round_down,
    round_normal,
    round_up,
    to_token_decimals,
)

pytestmark = pytest.mark.unit

KEY = "0x" + "ab" * 32
ADDRESS = "0x1111111111111111111111111111111111111111"


# ── rounding primitives (mirror py-clob-client helpers) ───────


def test_round_helpers() -> None:
    assert round_down(0.129, 2) == 0.12
    assert round_up(0.121, 2) == 0.13
    # Python's round() is banker's rounding: 0.125 -> 0.12, not 0.13.
    assert round_normal(0.125, 2) == 0.12
    assert round_normal(0.135, 2) == 0.14
    assert to_token_decimals(1.5) == 1_500_000


# ── order amounts ─────────────────────────────────────────────


def test_buy_amounts_are_usdc_then_shares() -> None:
    # BUY 10 shares @ 0.50 -> maker pays 5 USDC, taker receives 10 shares.
    maker, taker = order_amounts(SIDE_BUY, 10.0, 0.50, "0.01")
    assert maker == 5_000_000  # 5 USDC
    assert taker == 10_000_000  # 10 shares


def test_sell_amounts_are_shares_then_usdc() -> None:
    maker, taker = order_amounts(SIDE_SELL, 10.0, 0.50, "0.01")
    assert maker == 10_000_000  # 10 shares
    assert taker == 5_000_000  # 5 USDC


def test_invalid_side_raises() -> None:
    with pytest.raises(ValueError, match="side must be"):
        order_amounts(7, 10.0, 0.5, "0.01")


def test_tick_size_selects_rounding() -> None:
    # 0.001 tick allows 3 price decimals; 0.01 tick rounds price to 2.
    maker_fine, _ = order_amounts(SIDE_BUY, 1.0, 0.1234, "0.001")
    maker_coarse, _ = order_amounts(SIDE_BUY, 1.0, 0.1234, "0.01")
    assert maker_fine == to_token_decimals(round_normal(0.1234, 3))
    assert maker_coarse == to_token_decimals(round_normal(0.1234, 2))


# ── EIP-712 type hash ─────────────────────────────────────────


def test_order_type_hash_matches_manual_keccak() -> None:
    eth_utils = pytest.importorskip("eth_utils")

    expected = eth_utils.keccak(text=ORDER_TYPE_STRING)
    assert order_type_hash() == expected
    assert len(order_type_hash()) == 32


def test_side_and_signature_type_are_uint8() -> None:
    """uint256 here would change the type hash and break signatures."""
    assert "uint8 side" in ORDER_TYPE_STRING
    assert "uint8 signatureType" in ORDER_TYPE_STRING
    assert "uint256 side" not in ORDER_TYPE_STRING


# ── order struct ──────────────────────────────────────────────


def test_build_order_struct_fields() -> None:
    inputs = OrderInputs(
        token_id="12345",
        side=SIDE_BUY,
        size=10.0,
        price=0.50,
        tick_size="0.01",
        fee_rate_bps=0,
        expiration=0,
    )
    order = build_order_struct(
        wallet_address=ADDRESS,
        funder_address=ADDRESS,
        signature_type=SIG_TYPE_EOA,
        inputs=inputs,
        salt=42,
    )
    assert order["salt"] == 42
    assert order["maker"] == ADDRESS
    assert order["signer"] == ADDRESS
    assert order["taker"] == "0x" + "0" * 40
    assert order["tokenId"] == 12345
    assert order["side"] == SIDE_BUY
    assert order["signatureType"] == SIG_TYPE_EOA
    # Field order must match the canonical type string.
    assert list(order.keys()) == [
        "salt",
        "maker",
        "signer",
        "taker",
        "tokenId",
        "makerAmount",
        "takerAmount",
        "expiration",
        "nonce",
        "feeRateBps",
        "side",
        "signatureType",
    ]


# ── HMAC ──────────────────────────────────────────────────────


def test_hmac_message_format() -> None:
    assert build_hmac_message("100", "GET", "/book", None) == "100GET/book"
    assert build_hmac_message("100", "POST", "/order", '{"a": 1}') == '100POST/order{"a": 1}'


def test_hmac_message_normalises_single_quotes() -> None:
    """Go/TS clients send double quotes; ours must match byte-for-byte."""
    assert build_hmac_message("1", "POST", "/o", "{'a': 1}") == '1POST/o{"a": 1}'


def test_hmac_signature_is_base64url_and_deterministic() -> None:
    raw_secret = b"super-secret-value-1234"
    b64_secret = base64.urlsafe_b64encode(raw_secret).decode()
    sig = build_hmac_signature(b64_secret, "100", "GET", "/book", None)
    # Independent recomputation.
    msg = "100GET/book"
    expected = base64.urlsafe_b64encode(
        hmaclib.new(raw_secret, msg.encode(), hashlib.sha256).digest()
    ).decode()
    assert sig == expected
    assert build_hmac_signature(b64_secret, "100", "GET", "/book", None) == sig


def test_l2_headers_shape() -> None:
    b64_secret = base64.urlsafe_b64encode(b"s" * 16).decode()
    headers = l2_headers(
        address=ADDRESS,
        api_key="key",
        api_secret=b64_secret,
        api_passphrase="pass",
        method="GET",
        path="/book",
        timestamp=1234,
    )
    assert headers["POLY_ADDRESS"] == ADDRESS
    assert headers["POLY_API_KEY"] == "key"
    assert headers["POLY_PASSPHRASE"] == "pass"
    assert headers["POLY_TIMESTAMP"] == "1234"
    assert headers["POLY_SIGNATURE"]


# ── contract config (mainnet vs testnet must not mix) ─────────


def test_contract_configs_differ_between_chains() -> None:
    main = get_contract_config(CHAIN_POLYGON)
    amoy = get_contract_config(CHAIN_AMOY)
    assert main.exchange != amoy.exchange
    assert main.collateral != amoy.collateral
    assert main.conditional_tokens != amoy.conditional_tokens


def test_neg_risk_exchange_differs_from_regular() -> None:
    regular = get_contract_config(CHAIN_POLYGON, neg_risk=False)
    neg = get_contract_config(CHAIN_POLYGON, neg_risk=True)
    assert regular.exchange != neg.exchange


def test_unsupported_chain_raises() -> None:
    with pytest.raises(ValueError, match="unsupported chain"):
        get_contract_config(1)
