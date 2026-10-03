# polymm

Safety-first Polymarket bot: **market making + complete-set arbitrage** on
short-horizon crypto Up/Down markets, with a liquidity-reward overlay.
Designed to run on a small bankroll ($200–$500) with hard risk caps.

> **Status: early scaffold.** Only configuration, logging, and the test
> harness exist so far. The bot cannot trade yet — by design.

## Safety model

- **Fail-closed.** Missing or malformed config stops the process.
- **Dual-flag live.** Live orders require `enable_trading: true` **and**
  `mock_trading: false`. Any other combination is dry-run.
- **Separate hot wallet.** The bot uses a dedicated wallet holding only
  what you can afford to lose. Never your main wallet.
- **Secrets only from the environment.** Nothing sensitive is committed;
  logs are redacted unconditionally.
- **Bankroll-fraction sizing.** Every cap is a fraction of bankroll, so a
  small account scales down automatically.

## Layout

```
src/polymm/
  config.py     # validated config + safety gates
  logging.py    # mandatory secret redaction
tests/          # pytest suite (tiers below)
config.example.yaml
.env.example
```

## Setup

```bash
uv venv
uv pip install -e ".[dev]"
```

## Tests

Four tiers, cheapest first. Only tiers 1–2 run by default.

| Tier | Marker | Needs network? | Needs keys? | What it covers |
|------|--------|----------------|-------------|----------------|
| 1 | `unit` | no | no | pure logic: pricing, fees, skew, caps |
| 2 | `integration` | no | no | mock CLOB server, 429/timeout handling |
| 3 | `testnet` | yes (Amoy) | testnet key | real order signing/post/cancel |
| 4 | `e2e` | yes | mainnet key | full pipeline, dry-run only |

```bash
# Tier 1 + 2 (default)
uv run pytest

# Include testnet (needs POLYMM_TESTNET_PRIVATE_KEY)
uv run pytest -m "testnet"

# Coverage
uv run pytest --cov
```

## Testnet (Polygon Amoy, chain 80002)

Polymarket supports a testnet on Polygon **Amoy**. The contract and USDC
addresses **differ from mainnet** — do not reuse mainnet addresses:

| | Mainnet (137) | Amoy (80002) |
|---|---|---|
| CTF Exchange | `0x4bFb41d5…8982E` | `0xdFE02Eb6…9E40` |
| Neg-risk exchange | `0xC5d563A3…f80a` | same |
| USDC | `0x2791Bca1…4174` | `0x9c4e1703…fa78` |

**Testnet validates the order path, not the edge.** Liquidity, reward
bands, and fill realism only exist on mainnet — those are covered by
backtests and paper trading instead.

## Disclaimer

Educational/research software. Trading prediction markets carries real
financial risk. Validate with paper mode before committing capital.
