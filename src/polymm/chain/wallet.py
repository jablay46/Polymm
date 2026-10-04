"""Wallet and credential architecture (task 0.2).

Two ideas are enforced here:

1. **A dedicated hot wallet.** The bot is designed to sign with a wallet that
   holds only a small working balance. The *funder* (who owns the funds) may
   be a Polymarket proxy/Safe controlled by a different signer; we detect
   that case and select the matching signature type.

2. **Secrets never leave this boundary.** :class:`Wallet` holds the key only
   in memory. Its ``repr`` is redacted, and it never exposes the key through
   any accessor other than :meth:`Wallet.private_key_for_signing`, which is
   deliberately verbose so call sites are easy to audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from polymm.chain.constants import (
    SIG_TYPE_EOA,
    SIG_TYPE_POLY_GNOSIS_SAFE,
    SIG_TYPE_POLY_PROXY,
)
from polymm.config import Credentials

REDACTED: Final = "<redacted>"

# Signature type to use for a funder that differs from the signer. Polymarket
# uses 1 for an EOA-owned proxy and 2 for a Gnosis Safe; we cannot infer which
# from addresses alone, so callers must state it. Defaulting silently to one of
# them is how orders get rejected (or, worse, mis-signed).
_SIG_TYPES: dict[str, int] = {
    "proxy": SIG_TYPE_POLY_PROXY,
    "safe": SIG_TYPE_POLY_GNOSIS_SAFE,
}


class WalletError(ValueError):
    """Raised when wallet configuration is inconsistent or unusable."""


def is_address(value: str | None) -> bool:
    """True when *value* looks like a 20-byte hex address."""
    if not value or not value.startswith("0x") or len(value) != 42:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def normalize_address(value: str) -> str:
    """Lower-case a validated address; raise on malformed input."""
    if not is_address(value):
        raise WalletError(f"not a valid address: {value!r}")
    return value.lower()


@dataclass(frozen=True)
class Wallet:
    """A signing wallet plus the funder it acts for.

    ``funder`` equals the signer for a plain EOA. When it differs, the signer
    is an EOA that owns a Polymarket proxy wallet, which requires signature
    type ``POLY_PROXY`` for the exchange to accept the order.
    """

    address: str
    funder: str
    signature_type: int
    private_key: str

    def __repr__(self) -> str:
        return (
            f"Wallet(address={self.address}, funder={self.funder}, "
            f"signature_type={self.signature_type}, private_key={REDACTED})"
        )

    __str__ = __repr__

    @property
    def is_proxy(self) -> bool:
        return self.signature_type != SIG_TYPE_EOA

    def private_key_for_signing(self) -> str:
        """Return the raw key. Verbose name: grep for it when auditing.

        Every call site that reaches this method is a place where a secret
        exists in memory; keeping the name explicit makes review easy.
        """
        return self.private_key


def build_wallet(
    creds: Credentials,
    *,
    address_of: object | None = None,
    funder_type: str | None = None,
) -> Wallet:
    """Construct a :class:`Wallet` from credentials.

    Args:
        creds: Loaded credentials. A private key is required.
        address_of: Callable mapping a private key to its address. Injected so
            this module has no hard dependency on a crypto stack at import
            time (unit tests pass a stub; production passes the real one).
        funder_type: ``"proxy"`` or ``"safe"`` — required when the funder
            differs from the signer, because the exchange needs the correct
            signature type and it cannot be derived from the address.

    Raises:
        WalletError: when the key is missing/malformed, the funder address is
            invalid, or a distinct funder was given without ``funder_type``.
    """
    if not creds.private_key:
        raise WalletError("no private key configured (set POLYMM_PRIVATE_KEY)")

    key = creds.private_key.get_secret_value().strip()
    if not _looks_like_private_key(key):
        raise WalletError("private key must be 32 bytes of hex (64 chars, 0x-prefixed)")

    if address_of is None:
        raise WalletError("address_of callable is required to derive the signer address")

    signer_address = normalize_address(str(address_of(key)))  # type: ignore[operator]

    # Same address => plain EOA; different => the signer controls a proxy or
    # Safe wallet that holds the funds.
    funder = normalize_address(creds.funder_address) if creds.funder_address else signer_address
    if funder == signer_address:
        signature_type = SIG_TYPE_EOA
    else:
        if funder_type is None:
            raise WalletError(
                "funder differs from signer: set funder_type to 'proxy' (type 1) or 'safe' (type 2)"
            )
        try:
            signature_type = _SIG_TYPES[funder_type.strip().lower()]
        except KeyError as exc:
            raise WalletError(
                f"unknown funder_type {funder_type!r}; use 'proxy' or 'safe'"
            ) from exc

    return Wallet(
        address=signer_address,
        funder=funder,
        signature_type=signature_type,
        private_key=key,
    )


def _looks_like_private_key(value: str) -> bool:
    body = value[2:] if value.startswith("0x") else value
    if len(body) != 64:
        return False
    try:
        int(body, 16)
    except ValueError:
        return False
    return True
