"""Cross-check our order math against the official py-clob-client.

This test is the strongest guarantee that our amounts, type hash, and HMAC
payload match the real exchange expectations. It is skipped when the
``chain`` extra is not installed.
"""

# ruff: noqa: E402 — imports below must follow the importorskip guard.
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

py_clob_client = pytest.importorskip("py_clob_client")

from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.order_builder.helpers import (
    round_down as ref_round_down,
)
from py_clob_client.order_builder.helpers import (
    round_normal as ref_round_normal,
)
from py_clob_client.order_builder.helpers import (
    round_up as ref_round_up,
)
from py_clob_client.order_builder.helpers import (
    to_token_decimals as ref_to_decimals,
)
from py_clob_client.signing.hmac import build_hmac_signature as ref_hmac

from polymm.chain.constants import SIDE_BUY, SIDE_SELL, order_type_hash
from polymm.chain.signing import (
    build_hmac_signature,
    order_amounts,
    to_token_decimals,
)


@pytest.mark.parametrize("value,digits", [(0.129, 2), (0.121, 2), (0.125, 2), (1.2345, 3)])
def test_rounding_matches_reference(value: float, digits: int) -> None:
    from polymm.chain.signing import round_down, round_normal, round_up

    assert round_down(value, digits) == ref_round_down(value, digits)
    assert round_up(value, digits) == ref_round_up(value, digits)
    assert round_normal(value, digits) == ref_round_normal(value, digits)


@pytest.mark.parametrize("amount", [0.0, 1.5, 10.0, 123.456789, 999.999999])
def test_to_token_decimals_matches_reference(amount: float) -> None:
    assert to_token_decimals(amount) == ref_to_decimals(amount)


@pytest.mark.parametrize(
    ("side", "size", "price", "tick"),
    [
        (SIDE_BUY, 10.0, 0.50, "0.01"),
        (SIDE_SELL, 10.0, 0.50, "0.01"),
        (SIDE_BUY, 13.37, 0.4213, "0.001"),
        (SIDE_SELL, 7.77, 0.619, "0.01"),
    ],
)
def test_order_amounts_match_reference(side: int, size: float, price: float, tick: str) -> None:
    from py_clob_client.order_builder.builder import ROUNDING_CONFIG

    round_config = ROUNDING_CONFIG[tick]
    # Reference order builder is instance-based; replicate its amount logic
    # via the same helper functions it calls.
    raw_price = ref_round_normal(price, round_config.price)
    if side == SIDE_BUY:
        raw_taker = ref_round_down(size, round_config.size)
        raw_maker = raw_taker * raw_price
        if _dp(raw_maker) > round_config.amount:
            raw_maker = ref_round_up(raw_maker, round_config.amount + 4)
            if _dp(raw_maker) > round_config.amount:
                raw_maker = ref_round_down(raw_maker, round_config.amount)
        ref = (ref_to_decimals(raw_maker), ref_to_decimals(raw_taker))
    else:
        raw_maker = ref_round_down(size, round_config.size)
        raw_taker = raw_maker * raw_price
        if _dp(raw_taker) > round_config.amount:
            raw_taker = ref_round_up(raw_taker, round_config.amount + 4)
            if _dp(raw_taker) > round_config.amount:
                raw_taker = ref_round_down(raw_taker, round_config.amount)
        ref = (ref_to_decimals(raw_maker), ref_to_decimals(raw_taker))

    assert order_amounts(side, size, price, tick) == ref


def test_side_constants_match_reference() -> None:
    """The EIP-712 struct uses numeric sides; the builder's BUY/SELL are strings."""
    from py_order_utils.model.sides import BUY as NUM_BUY
    from py_order_utils.model.sides import SELL as NUM_SELL

    assert SIDE_BUY == NUM_BUY
    assert SIDE_SELL == NUM_SELL
    # The string constants are for the REST payload, not the signed struct.
    assert BUY == "BUY"
    assert SELL == "SELL"


def test_hmac_matches_reference() -> None:
    import base64

    secret = base64.urlsafe_b64encode(b"another-secret-value-99").decode()
    ours = build_hmac_signature(secret, "1700000000", "POST", "/order", '{"x": 1}')
    ref = ref_hmac(secret, "1700000000", "POST", "/order", '{"x": 1}')
    assert ours == ref


def test_type_hash_is_stable() -> None:
    # A fixed, reviewed value: if this changes, signatures will break.
    assert order_type_hash().hex() == (
        "a852566c4e14d00869b6db0220888a9090a13eccdaea03713ff0a3d27bf9767c"
    )


@pytest.mark.parametrize("chain_id", [137, 80002])
def test_signature_matches_official_builder(chain_id: int) -> None:
    """Our signed order must be byte-identical to py_order_utils' output.

    This is the strongest correctness check available offline: the real
    exchange verifies exactly this signature.
    """
    from py_clob_client.config import get_contract_config as ref_contract_config
    from py_order_utils.builders import OrderBuilder as RefBuilder
    from py_order_utils.constants import ZERO_ADDRESS as REF_ZERO
    from py_order_utils.model import EOA as REF_EOA
    from py_order_utils.model import OrderData as RefOrderData
    from py_order_utils.model.sides import BUY as REF_BUY
    from py_order_utils.signer import Signer as RefSigner

    from polymm.chain.constants import SIDE_BUY
    from polymm.chain.signing import (
        OrderInputs,
        build_order_struct,
        sign_order,
    )

    key = "0x" + "ab" * 32
    salt = 12345
    contract = ref_contract_config(chain_id)
    signer = RefSigner(key)
    address = signer.address()

    ref_builder = RefBuilder(contract.exchange, chain_id, signer, salt_generator=lambda: salt)
    ref = ref_builder.build_signed_order(
        RefOrderData(
            maker=address,
            signer=address,
            taker=REF_ZERO,
            tokenId="12345",
            makerAmount="5000000",
            takerAmount="10000000",
            side=REF_BUY,
            feeRateBps="0",
            nonce="0",
            expiration="0",
            signatureType=REF_EOA,
        )
    ).signature

    order = build_order_struct(
        wallet_address=address,
        funder_address=address,
        signature_type=0,  # EOA
        inputs=OrderInputs(
            token_id="12345", side=SIDE_BUY, size=10.0, price=0.50, tick_size="0.01"
        ),
        salt=salt,
    )
    mine = sign_order(
        order=order,
        private_key=key,
        chain_id=chain_id,
        verifying_contract=contract.exchange,
    )
    assert mine == ref


def _dp(x: float) -> int:
    from decimal import Decimal

    return abs(Decimal(x.__str__()).as_tuple().exponent)
