"""Polymarket contract addresses and EIP-712 constants.

Values mirror the official ``py-clob-client`` ``config.py`` exactly. Mainnet
(137) and Amoy testnet (80002) use **different** addresses — mixing them is
a common and expensive mistake, so they are kept in separate, named tables.
"""

from __future__ import annotations

from dataclasses import dataclass

# Chain ids.
CHAIN_POLYGON = 137
CHAIN_AMOY = 80002

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Signature types (see py_order_utils.model.signatures).
SIG_TYPE_EOA = 0
SIG_TYPE_POLY_PROXY = 1
SIG_TYPE_POLY_GNOSIS_SAFE = 2

# Order sides (see py_order_utils.model.sides).
SIDE_BUY = 0
SIDE_SELL = 1

# CLOB L1 auth message (ClobAuthDomain).
CLOB_DOMAIN_NAME = "ClobAuthDomain"
CLOB_DOMAIN_VERSION = "1"
CLOB_AUTH_MESSAGE = "This message attests that I control the given wallet"

# CTF Exchange EIP-712 domain. Unlike ClobAuthDomain this is NOT a fixed
# literal: it is read from the deployed contract, so name/version can differ.
EXCHANGE_DOMAIN_NAME = "Polymarket CTF Exchange"
EXCHANGE_DOMAIN_VERSION = "1"

# CLOB V2 (live since 2026-04-28). V1 signing was retired; V1-signed orders
# are rejected with ``order_version_mismatch``. V2 uses the same EIP-712
# Order type hash and the same ClobAuth domain (still version "1"), but a
# bumped Exchange domain version ("2"), new exchange contracts, and pUSD as
# collateral instead of USDC.e. The order *struct* changed too (nonce,
# feeRateBps, taker removed; timestamp, metadata, builder added) — see
# docs/CLOB_V2.md. The live adapter does not sign V2 yet, so preflight treats
# a V1 config as a hard stop for live trading.
CLOB_V2_EXCHANGE_DOMAIN_VERSION = "2"
CLOB_V2_ACTIVATION = "2026-04-28"

# EIP-712 Order type. Field order is significant — the type hash embeds it.
# `side` and `signatureType` are uint8 (NOT uint256); using uint256 produces
# a different type hash and the exchange will reject the signature.
ORDER_TYPE_FIELDS = (
    "uint256 salt",
    "address maker",
    "address signer",
    "address taker",
    "uint256 tokenId",
    "uint256 makerAmount",
    "uint256 takerAmount",
    "uint256 expiration",
    "uint256 nonce",
    "uint256 feeRateBps",
    "uint8 side",
    "uint8 signatureType",
)
ORDER_TYPE_STRING = "Order(" + ",".join(ORDER_TYPE_FIELDS) + ")"


@dataclass(frozen=True)
class ContractConfig:
    exchange: str
    collateral: str
    conditional_tokens: str


# Regular (non-negative-risk) markets.
_CONTRACTS: dict[int, ContractConfig] = {
    CHAIN_POLYGON: ContractConfig(
        exchange="0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        collateral="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        conditional_tokens="0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    ),
    CHAIN_AMOY: ContractConfig(
        exchange="0xdFE02Eb6733538f8Ea35D585af8DE5958AD99E40",
        collateral="0x9c4e1703476e875070ee25b56a58b008cfb8fa78",
        conditional_tokens="0x69308FB512518e39F9b16112fA8d994F4e2Bf8bB",
    ),
}

# Negative-risk markets use a different exchange deployment.
_NEG_RISK_CONTRACTS: dict[int, ContractConfig] = {
    CHAIN_POLYGON: ContractConfig(
        exchange="0xC5d563A36AE78145C45a50134d48A1215220f80a",
        collateral="0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
        conditional_tokens="0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    ),
    CHAIN_AMOY: ContractConfig(
        exchange="0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
        collateral="0x9c4e1703476e875070ee25b56a58b008cfb8fa78",
        conditional_tokens="0x69308FB512518e39F9b16112fA8d994F4e2Bf8bB",
    ),
}


def get_contract_config(chain_id: int, neg_risk: bool = False) -> ContractConfig:
    """Return the exchange/collateral/CTF addresses for a chain.

    Raises:
        ValueError: unsupported chain id.
    """
    table = _NEG_RISK_CONTRACTS if neg_risk else _CONTRACTS
    config = table.get(chain_id)
    if config is None:
        raise ValueError(f"unsupported chain id: {chain_id}")
    return config


# CLOB V2 contract addresses (Polygon mainnet), live since 2026-04-28.
# Neg-risk has two exchange deployments in V2; the primary is listed here.
# pUSD (0xc011a7...) replaces USDC.e as collateral.
_CONTRACTS_V2: dict[int, ContractConfig] = {
    CHAIN_POLYGON: ContractConfig(
        exchange="0xe111180000d2663c0091e4f400237545b87b996b",
        collateral="0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb",
        conditional_tokens="0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    ),
}

_NEG_RISK_CONTRACTS_V2: dict[int, ContractConfig] = {
    CHAIN_POLYGON: ContractConfig(
        exchange="0xe2222d279d744050d28e00520010520000310f59",
        collateral="0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb",
        conditional_tokens="0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    ),
}


def get_contract_config_v2(chain_id: int, neg_risk: bool = False) -> ContractConfig:
    """Return the CLOB V2 exchange/collateral/CTF addresses for a chain.

    Raises:
        ValueError: chain has no V2 deployment (only Polygon mainnet does).
    """
    table = _NEG_RISK_CONTRACTS_V2 if neg_risk else _CONTRACTS_V2
    config = table.get(chain_id)
    if config is None:
        raise ValueError(f"no CLOB V2 contracts for chain id: {chain_id}")
    return config


def order_type_hash() -> bytes:
    """Keccak-256 of the canonical Order type string."""
    from eth_utils import keccak

    return bytes(keccak(text=ORDER_TYPE_STRING))
