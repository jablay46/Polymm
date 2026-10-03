# AGENTS.md — polymm

Repository memory for agent sessions. Read this first.

## What this is

A safety-first Polymarket bot: **market making + complete-set arbitrage**
on short-horizon crypto Up/Down markets, with a liquidity-reward overlay.
Target bankroll $200–$500. Built incrementally; the bot cannot trade yet.

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

## Commands

```bash
uv venv && uv pip install -e ".[dev]" types-PyYAML
uv run pytest                 # tiers 1+2 (default; no secrets)
uv run pytest --cov           # coverage gate is 90%
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest -m "testnet"    # needs POLYMM_TESTNET_PRIVATE_KEY
```

## Layout

```
src/polymm/config.py    # validated config + safety gates (DONE)
src/polymm/logging.py   # mandatory secret redaction (DONE)
tests/                  # conftest.py has synthetic Book/Level fixtures
```

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

Done: 0.1, 0.3, 0.4, 0.5.1, 0.5.2, 0.5.3.
Next: 0.2 (wallet/credential architecture), then 0.5.4–0.5.16.

See the conversation task tracker for the full 45-task plan.
