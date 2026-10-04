# CLOB V2 migration — why live trading is currently fail-closed

Polymarket cut production over to **CLOB V2** on **2026-04-28 (~11:00 UTC)**.
There is no backward compatibility: V1 SDKs and V1-signed orders stopped
working that day and every open order was wiped at the cutover. V1 orders are
now rejected with `order_version_mismatch`.

The pinned dependency, `py-clob-client`, still ships **only V1** contract
addresses (verified: `py_clob_client/config.py` maps chain 137 to the old
`0x4bFb…` exchange and `0x2791…` USDC.e). So this bot cannot place a valid
live order today, and pretending otherwise would be worse than stopping.

## What changed in V2

| Area | V1 | V2 |
|------|----|----|
| Exchange domain version (EIP-712) | `"1"` | `"2"` |
| CTF Exchange (Polygon) | `0x4bFb41d5…8982E` | `0xe111180000d2663c0091e4f400237545b87b996b` |
| Neg-risk exchange | `0xC5d563A3…f80a` | `0xe2222d279d744050d28e00520010520000310f59` |
| Collateral | USDC.e `0x2791Bca1…4174` | pUSD `0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb` |
| Order struct | has `nonce`, `feeRateBps`, `taker` | those removed; `timestamp` (ms), `metadata`, `builder` added |
| Fees | on the order (`feeRateBps`) | set at match time |
| ClobAuth domain | `"1"` | unchanged (`"1"`) |
| ConditionalTokens | `0x4D97DCd9…6045` | unchanged |

The EIP-712 **Order type hash is unchanged**, so the signing math in
`chain/signing.py` remains valid — what differs is the domain version, the
exchange address, and the order struct that gets signed.

## Current behaviour (fail-closed)

`preflight` raises a hard `ERROR` (`clob_v1_protocol`) whenever
`exchange.domain_version != "2"`, so a V1 config can never place a live
order. It also cross-checks the configured exchange and neg-risk addresses
against the known deployment for the chain and version, so a stale config
that points at the wrong contract is caught before trading.

Paper trading (`polymm paper`) and all unit tests are unaffected — they never
touch the live venue.

## Migrating the adapter (checklist)

1. Upgrade to a V2-capable client (or sign orders directly against the V2
   domain). Do not hand-roll the struct change without the official ABI.
2. In the YAML `exchange` section, set `domain_version: "2"` and the V2
   exchange/neg-risk addresses from the table above. Preflight will verify
   them.
3. Update `chain/constants.py` consumers to use `get_contract_config_v2`
   for the live path.
4. Handle pUSD: balances and allowances are now against pUSD, not USDC.e.
5. Re-run `preflight` — it must report no `ERROR` before any live order.
6. Exercise tier-3 testnet tests, then a dry-run, before enabling live.

Until that work is done, keep `enable_trading: false`. The strategy, risk,
and paper-trading paths are fully usable and tested today.
