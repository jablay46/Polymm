"""Tests for the wallet/credential architecture (task 0.2)."""

from __future__ import annotations

import pytest

from polymm.chain.constants import SIG_TYPE_EOA, SIG_TYPE_POLY_PROXY
from polymm.chain.wallet import (
    WalletError,
    build_wallet,
    is_address,
    normalize_address,
)
from polymm.config import Credentials

pytestmark = pytest.mark.unit

KEY = "0x" + "ab" * 32
SIGNER = "0x1111111111111111111111111111111111111111"
PROXY = "0x2222222222222222222222222222222222222222"


def _address_of(_key: str) -> str:
    return SIGNER


# ── address helpers ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (SIGNER, True),
        (SIGNER.upper().replace("0X", "0x"), True),
        ("0x123", False),
        ("1111111111111111111111111111111111111111", False),  # no 0x
        ("0x" + "zz" * 20, False),  # non-hex
        (None, False),
        ("", False),
    ],
)
def test_is_address(value, expected) -> None:
    assert is_address(value) is expected


def test_normalize_lowercases() -> None:
    assert normalize_address(SIGNER.upper().replace("0X", "0x")) == SIGNER


def test_normalize_rejects_bad() -> None:
    with pytest.raises(WalletError):
        normalize_address("nope")


# ── EOA vs proxy detection ────────────────────────────────────


def test_eoa_when_funder_absent() -> None:
    w = build_wallet(Credentials(private_key=KEY), address_of=_address_of)
    assert w.funder == SIGNER
    assert w.signature_type == SIG_TYPE_EOA
    assert w.is_proxy is False


def test_eoa_when_funder_equals_signer() -> None:
    w = build_wallet(
        Credentials(private_key=KEY, funder_address=SIGNER),
        address_of=_address_of,
    )
    assert w.signature_type == SIG_TYPE_EOA


def test_proxy_when_funder_differs() -> None:
    w = build_wallet(
        Credentials(private_key=KEY, funder_address=PROXY),
        address_of=_address_of,
    )
    assert w.funder == PROXY
    assert w.signature_type == SIG_TYPE_POLY_PROXY
    assert w.is_proxy is True


# ── fail-closed ───────────────────────────────────────────────


def test_missing_key_raises() -> None:
    with pytest.raises(WalletError, match="no private key"):
        build_wallet(Credentials(), address_of=_address_of)


def test_short_key_raises() -> None:
    with pytest.raises(WalletError, match="32 bytes"):
        build_wallet(Credentials(private_key="0xabc"), address_of=_address_of)


def test_bad_funder_raises() -> None:
    with pytest.raises(WalletError):
        build_wallet(
            Credentials(private_key=KEY, funder_address="not-an-address"),
            address_of=_address_of,
        )


# ── secret hygiene ────────────────────────────────────────────


def test_wallet_repr_redacted() -> None:
    w = build_wallet(Credentials(private_key=KEY), address_of=_address_of)
    text = repr(w)
    assert KEY not in text
    assert "ab" * 32 not in text
    assert "<redacted>" in text
    assert SIGNER in text  # addresses are not secret


def test_key_only_via_explicit_accessor() -> None:
    w = build_wallet(Credentials(private_key=KEY), address_of=_address_of)
    assert w.private_key_for_signing() == KEY


def test_wallet_is_frozen() -> None:
    w = build_wallet(Credentials(private_key=KEY), address_of=_address_of)
    with pytest.raises((AttributeError, TypeError)):
        w.address = "0x" + "0" * 40  # type: ignore[misc]
