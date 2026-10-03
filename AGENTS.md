# AGENTS.md — polymm

Repository memory for agent sessions. Read this first.

## What this is

A safety-first Polymarket bot: **market making + complete-set arbitrage**
on short-horizon crypto Up/Down markets, with a liquidity-reward overlay.
Target bankroll $200–$500. Strategy, execution, risk, and the runnable
paper path are implemented and tested; live signing is untested on mainnet.

## Hard rules (do not violate)

1. **Never commit a secret.** No private key, API secret, passphrase, or
   signature in any tracked file. Secrets come from the environment only.
2. **Fail-closed.** New config must default to the safe value; a missing or
   malformed config must stop the process, not degrade silently.
3. **Dual-flag live.** Live orders require `enable_trading=True` AND
   `mock_trading=False`. Never add a path that bypasses this.
4. **Every module ships with its unit test.** A module is not "done" until
   its tier-1 tests exist and pass.
5. **No network or credentials in tier-1/2 tests.** They must run on a fork.
6. **Gitignore patterns must be root-anchored.** `data/`, `logs/`, `state/`
   are written as `/data/` etc. An unanchored `data/` also matches
   `src/polymm/data/` and silently drops the source package from git.
   Before committing, confirm `git ls-files src/polymm | wc -l` matches the
   number of modules on disk.
7. **CI installs only `.[dev]`.** The `chain` extra is optional, so mypy and
   pytest must pass with it absent (chain-only imports are `importorskip`'d
   or lazily imported). Keep `[tool.mypy.overrides]` in sync.

## Commands

```bash
uv venv && uv pip install -e ".[dev,chain]" types-PyYAML
uv run pytest                 # tiers 1+2 (default; no secrets)
uv run pytest --cov           # coverage gate is 90%
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest -m "testnet"    # needs POLYMM_TESTNET_PRIVATE_KEY
```

## Layout

```
src/polymm/config.py         # validated config + safety gates (DONE)
src/polymm/logging.py        # mandatory secret redaction (DONE)
src/polymm/chain/
  constants.py               # mainnet/Amoy addresses, EIP-712 type (DONE)
  wallet.py                  # hot wallet, EOA vs proxy detection (DONE)
  signing.py                 # EIP-712 order + L1/L2 headers (DONE)
src/polymm/data/decoder.py   # OrderFilled log -> Trade (DONE)
src/polymm/pricing.py        # Decimal book walk + fee model (DONE)
src/polymm/market.py         # OrderBook model, always sorted (DONE)
src/polymm/risk.py           # caps + kill switch (DONE)
src/polymm/exchange.py       # Exchange protocol + live CLOB adapter (DONE)
src/polymm/paper.py          # in-memory paper venue (DONE)
src/polymm/strategy.py       # complete-set arb strategy (DONE)
src/polymm/execution.py      # two-leg executor + unwind (DONE)
src/polymm/runner.py         # scan loop (DONE)
src/polymm/paper_run.py      # runnable paper path (DONE)
src/polymm/data/gamma.py     # market discovery (Gamma) (DONE)
src/polymm/preflight.py      # fail-closed live checks (DONE)
src/polymm/cli.py            # `polymm` entrypoint (DONE)
tests/                       # conftest.py has synthetic Book/Level fixtures
```

## Facts verified against the official py-clob-client

Do not "improve" these from memory — they are checked against
`py_order_utils` / `py_clob_client` source:

- Order type hash `0xa852566c…767c`. `side` and `signatureType` are **uint8**,
  not uint256. Field order: salt, maker, signer, taker, tokenId,
  makerAmount, takerAmount, expiration, nonce, feeRateBps, side,
  signatureType.
- Sides are numeric: BUY=0, SELL=1 (the builder's `"BUY"`/`"SELL"` strings
  are only for the REST payload).
- Signature types: EOA=0, POLY_PROXY=1, POLY_GNOSIS_SAFE=2.
- CTF Exchange domain is `"Polymarket CTF Exchange"` v`"1"`; ClobAuth domain
  is `"ClobAuthDomain"` v`"1"` with no verifying contract.
- L2 HMAC payload = `timestamp + METHOD + path + body`, body single quotes
  replaced with double quotes, keyed by **base64-decoded** api_secret,
  returned base64url.
- Amoy neg-risk exchange is `0xd91E80cF…296`, distinct from the regular one.
- `round()` is banker's rounding: `round_normal(0.125, 2) == 0.12`.
- Fees are per-share `fee_rate * p * (1-p)` (peak at 0.5). All money
  math uses Decimal, never float.
- OrderFilled has **three indexed params** -> 4 topics:
  `[sig, orderHash, maker, taker]`. Topic0 = `0x91105c38…706d`.
  USDC asset id is 0; amounts are 6-decimal.

`tests/test_signing_reference.py` asserts our signed order is **byte-identical**
to `py_order_utils`' output on both chains. Keep that test passing.

## Test tiers

| Tier | Marker | Network | Keys | Covers |
|------|--------|---------|------|--------|
| 1 | `unit` | no | no | pricing, fees, skew, caps, decoding |
| 2 | `integration` | no | no | mock CLOB server, 429/timeout |
| 3 | `testnet` | Amoy | testnet key | real order signing/post/cancel |
| 4 | `e2e` | yes | mainnet key | full pipeline, dry-run only |

Testnet/e2e are excluded by default via `addopts`.

## Environment variables

`POLYMM_PRIVATE_KEY`, `POLYMM_FUNDER_ADDRESS`, `POLYMM_API_KEY`,
`POLYMM_API_SECRET`, `POLYMM_API_PASSPHRASE`,
`POLYMM_TESTNET_PRIVATE_KEY`, `POLYMM_ENABLE_TRADING`, `POLYMM_MOCK_TRADING`.
Names live as constants in `config.py` — update both together.

## Testnet facts (Polygon Amoy, chain 80002)

Mainnet and Amoy use **different** addresses — never mix them:

| | Mainnet (137) | Amoy (80002) |
|---|---|---|
| CTF Exchange | `0x4bFb41d5…8982E` | `0xdFE02Eb6…9E40` |
| Neg-risk exchange | `0xC5d563A3…f80a` | same |
| USDC | `0x2791Bca1…4174` | `0x9c4e1703…fa78` |

Testnet validates the **order path**, not the **edge**. Liquidity, reward
bands, and fill realism only exist on mainnet (backtest/paper instead).

## Design notes

- Sizing caps are **fractions of bankroll** so small accounts scale down.
- Fee model for edge math: `fee = rate * price * (1 - price)`.
- Complete-set edge: `1 - (bid_up + bid_dn)`, evaluated **net of fees**.
- Maker quote: `min(bestBid + tick*(improve+skew), mid)`, round DOWN,
  never cross the spread.
- Redaction is unconditional; it is not a config option.

## Progress

Done: 0.1, 0.2, 0.3, 0.4, 0.5.1–0.5.16. Phase 0.5 complete.
Runnable: `uv run python -m polymm paper --cycles 3` and
`uv run python -m polymm preflight <config>`.

See the conversation task tracker for the full 45-task plan.
