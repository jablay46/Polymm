# polymm

Safety-first Polymarket bot: **market making + complete-set arbitrage** on
short-horizon crypto Up/Down markets, with a liquidity-reward overlay.
Designed to run on a small bankroll ($200–$500) with hard risk caps.

> **Status: the strategy, execution, risk, and paper-trading path are
> implemented and tested. Live order signing targets CLOB V1, which
> Polymarket retired on 2026-04-28 — preflight therefore refuses to go live
> until the adapter is migrated. Run paper mode; see `docs/CLOB_V2.md`.**

## Quick start (no keys, no network)

```bash
uv venv && uv pip install -e ".[dev]"

# Runnable paper-trading smoke test
uv run python -m polymm paper --cycles 3
# or, after install:
uv run polymm paper --cycles 3

# Full test suite + coverage
uv run pytest --cov
```

`paper` builds synthetic binary markets, runs the real
strategy/risk/execution pipeline against an in-memory venue, and prints a
report. It exits non-zero if any unhedged position was created.

## Preflight before going live

```bash
uv run python -m polymm preflight config.example.yaml --bankroll 300
```

Any `[ERROR]` line means the bot refuses to trade. Checks: dual-flag live
gate, signer present, known chain contracts **and that the configured
addresses match the deployment for the configured CLOB version**, sane risk
caps, minimum bankroll, and a hard stop on the retired CLOB V1 protocol.
`[WARN]` lines are surfaced but do not stop the run.

## Strategy: complete-set arbitrage

A binary market's YES and NO shares always redeem for exactly $1 combined.
If you can buy both legs for a total of less than $1 (after fees), you hold
a risk-free complete set. The bot:

1. Scans Gamma-discovered markets, fetching both CLOB order books.
2. Walks the real books for a true VWAP (not just top-of-book), charging
   the taker fee `fee_rate · p · (1 − p)` per share.
3. Requires net basket cost ≤ `max_basket_cost` **and** edge ≥ `min_edge`.
4. Sizes by the thinnest visible side, capped by the target size.
5. Executes the **cheaper leg first** (it reprices away first), requires a
   full FOK/IOC fill, and — if the second leg fails — immediately **unwinds**
   the first leg. A failed unwind is flagged `UNHEDGED`, loudly.

Every gate is a hard, configurable stop; every number is a `Decimal`.

## Safety model

- **Fail-closed.** Missing or malformed config stops the process.
- **Dual-flag live.** Live orders require `enable_trading: true` **and**
  `mock_trading: false`. Any other combination is dry-run.
- **Non-atomic fills handled explicitly.** Two legs are two orders; the
  executor never leaves a silent naked position.
- **Kill switch.** Daily loss limit (auto-rolls at midnight) plus
  large-trade burst detection; both auto-expire.
- **Bankroll-fraction sizing.** Every cap is a fraction of bankroll, so a
  small account scales down automatically.
- **Separate hot wallet.** Use a dedicated wallet holding only what you can
  afford to lose. Never your main wallet.
- **Secrets only from the environment.** Nothing sensitive is committed;
  logs are redacted unconditionally.

## Layout

```
src/polymm/
  config.py       # validated config + safety gates
  pricing.py      # Decimal tick/VWAP/fee math
  market.py       # OrderBook + Market (YES/NO pair)
  strategy.py     # complete-set arbitrage
  execution.py    # two-leg executor with unwind
  risk.py         # caps + kill switch
  exchange.py     # Exchange Protocol + live CLOB adapter
  paper.py        # in-memory venue
  runner.py       # scan loop
  paper_run.py    # runnable paper path
  preflight.py    # fail-closed live checks
  cli.py          # `polymm` entrypoint
  data/gamma.py   # market discovery
  logging.py      # mandatory secret redaction
tests/            # pytest suite (tiers below)
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

## License

[MIT](LICENSE) © 2026 jablay46
