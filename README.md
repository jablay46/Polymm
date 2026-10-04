# polymm

Safety-first Polymarket bot: **market making + complete-set arbitrage** on
short-horizon crypto Up/Down markets, with a liquidity-reward overlay.
Designed to run on a small bankroll ($200–$500) with hard risk caps.

> **Status: the strategy, execution, risk, and paper-trading path are
> implemented and tested. Live order signing targets CLOB V1, which
> Polymarket retired on 2026-04-28 — preflight therefore refuses to go live
> until the adapter is migrated. Run paper mode; see `docs/CLOB_V2.md`.**

## Table of contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Installation](#installation)
  - [1. Get the code](#1-get-the-code)
  - [2. Install Python](#2-install-python)
  - [3. Create a virtual environment](#3-create-a-virtual-environment)
  - [4. Install the package](#4-install-the-package)
  - [5. Verify the install](#5-verify-the-install)
- [Configure](#configure)
- [Run paper trading](#run-paper-trading)
- [Preflight before going live](#preflight-before-going-live)
- [Strategy: complete-set arbitrage](#strategy-complete-set-arbitrage)
- [Safety model](#safety-model)
- [Layout](#layout)
- [Tests](#tests)
- [Testnet (Polygon Amoy, chain 80002)](#testnet-polygon-amoy-chain-80002)
- [Troubleshooting](#troubleshooting)
- [Disclaimer](#disclaimer)
- [License](#license)

## What it does

The bot finds binary markets where YES + NO can be bought for less than $1
combined, then holds the pair to redemption for a risk-free edge. It is
built to be boring and safe: every limit is a hard stop, every number is a
`Decimal`, and nothing touches a wallet or the network until you
explicitly opt in.

## Requirements

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.11 or newer** (3.12 tested in CI) | `requires-python = ">=3.11"` |
| OS | Linux, macOS, Windows | WSL2 recommended on Windows |
| Disk | ~200 MB | virtualenv + dependencies |
| Network | only for live mode | paper mode and tests run fully offline |
| Bankroll | $200–$500 | sizing caps are bankroll fractions |

`uv` is recommended but **optional** — plain `venv` + `pip` works too
(shown in [Installation](#installation)).

## Installation

### 1. Get the code

```bash
git clone https://github.com/jablay46/Polymm.git
cd Polymm
```

If you do not have Git, install it first:

- **Debian/Ubuntu:** `sudo apt update && sudo apt install -y git`
- **macOS:** `brew install git` (or `xcode-select --install`)
- **Windows:** `winget install --id Git.Git -e` or download from
  <https://git-scm.com/downloads>

### 2. Install Python

You need Python **3.11+**. Check what you have:

```bash
python3 --version
```

If it is older than 3.11 (or missing), install it:

- **Debian/Ubuntu:**

  ```bash
  sudo apt update
  sudo apt install -y python3 python3-venv python3-pip
  ```

  Distro packages can lag behind 3.11. If `python3 --version` is too old,
  use the deadsnakes PPA (`sudo add-apt-repository ppa:deadsnakes/ppa &&
  sudo apt install python3.12 python3.12-venv`) or install
  [uv](https://docs.astral.sh/uv/) and let it manage Python for you
  (`uv python install 3.12`).

- **macOS:** `brew install python@3.12`
- **Windows:** `winget install --id Python.Python.3.12 -e` (tick "Add to
  PATH" in the installer)

### 3. Create a virtual environment

Always work inside a virtualenv — never install into the system Python.

```bash
python3 -m venv .venv
```

Activate it:

```bash
# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

# Windows cmd.exe
.venv\Scripts\activate.bat
```

### 4. Install the package

Pick **one** of the two paths below.

**Option A — `uv` (recommended, reproducible via `uv.lock`):**

```bash
# Install uv if you do not have it:
#   curl -LsSf https://astral.sh/uv/install.sh | sh              (Linux/macOS)
#   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"   (Windows)

uv venv                              # creates .venv
uv sync --extra dev                  # dev tools + runtime deps, locked
uv sync --extra dev --extra chain    # ... plus wallet/order signing
```

`uv sync --locked` is what CI uses; it installs **exactly** the lockfile
and removes anything else, so do not `pip install` extra packages into a
`uv`-managed venv.

**Option B — plain `venv` + `pip`:**

```bash
# inside the activated .venv
python -m pip install --upgrade pip
pip install -e ".[dev]"              # dev tools + runtime deps
pip install -e ".[dev,chain]"        # ... plus wallet/order signing
```

What the extras mean:

| Extra | Contents | Needed for |
|---|---|---|
| *(none)* | `pydantic`, `pydantic-settings`, `PyYAML` | paper mode, config, preflight |
| `chain` | `py-clob-client`, `eth-account`, `eth-utils`, `poly-eip712-structs`, `py-order-utils` | live signing / testnet order tests |
| `dev` | `pytest`, `pytest-cov`, `ruff`, `mypy`, `types-PyYAML`, `pre-commit` | development and tests |

> **Secrets:** the `chain` extra pulls in a crypto stack, but it does
> **not** read any key at import time. Keys are only read from the
> environment when a live order is actually placed.

### 5. Verify the install

```bash
# Runs offline, needs no keys. Prints a paper-trading report.
uv run python -m polymm paper --cycles 3
# or, if the venv is active / the console script is on PATH:
polymm paper --cycles 3
```

Expected output (numbers are synthetic and deterministic):

```
── paper trading report ─────────────────────────
markets scanned : 6
intents found   : 1
skipped (errors): 0
fills           : 1
naked positions : 0
realized edge   : $0.3002
cash remaining  : $190.3002
```

Then run the test suite:

```bash
uv run pytest --cov        # or: pytest --cov
```

## Configure

```bash
cp config.example.yaml config.yaml   # config.yaml is gitignored
cp .env.example .env                 # .env is gitignored
```

- `config.yaml` holds **non-secret** settings: trading gates, endpoints,
  risk caps, strategy thresholds. Defaults are safe (`enable_trading:
  false`, `mock_trading: true`).
- `.env` holds **secrets only**: `POLYMM_PRIVATE_KEY`,
  `POLYMM_API_KEY`, `POLYMM_API_SECRET`, `POLYMM_API_PASSPHRASE`,
  `POLYMM_TESTNET_PRIVATE_KEY`.

Never commit either file. A leaked private key cannot be un-leaked — use a
dedicated hot wallet holding only what you can afford to lose.

## Run paper trading

Paper mode builds synthetic binary markets and runs the **real**
strategy/risk/execution pipeline against an in-memory venue. No keys, no
network. It exits non-zero if any unhedged position was created, so it
works as a smoke test in CI.

```bash
python -m polymm paper --cycles 3
python -m polymm paper --cycles 10 --bankroll 500 --target-size 20 --fee-rate 0.02
```

| Flag | Default | Meaning |
|---|---|---|
| `--cycles` | `1` | number of scan/execute loops |
| `--interval` | `1.0` | seconds between cycles |
| `--bankroll` | `200` | starting cash (USD) |
| `--fee-rate` | `0.02` | taker fee rate |
| `--target-size` | `10` | target shares per leg |

## Preflight before going live

```bash
python -m polymm preflight config.yaml --bankroll 300
```

Any `[ERROR]` line means the bot refuses to trade (exit code 1), so it can
gate a deployment:

```
[ERROR] trading_disabled: enable_trading is false
[ERROR] mock_enabled: mock_trading is true
[ERROR] live_gate: live requires enable_trading=true AND mock_trading=false
[ERROR] no_signer: no private key for order signing
[WARN ] no_l2: no L2 (HMAC) credentials; authenticated endpoints may fail
[ERROR] clob_v1_protocol: live signing is still on CLOB V1 ...
```

Checks: dual-flag live gate, signer present, known chain contracts **and
that the configured addresses match the deployment for the configured CLOB
version**, sane risk caps, minimum bankroll, and a hard stop on the retired
CLOB V1 protocol. `[WARN]` lines are surfaced but do not stop the run.

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
  wiring.py       # config -> runtime wiring
  cli.py          # `polymm` entrypoint
  data/gamma.py   # market discovery
  logging.py      # mandatory secret redaction
tests/            # pytest suite (tiers below)
config.example.yaml
.env.example
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

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'polymm'` | venv not active, or package not installed | activate `.venv` and re-run step 4 |
| `No module named 'py_clob_client'` | `chain` extra not installed | `uv sync --extra dev --extra chain` or `pip install -e ".[dev,chain]"` |
| `command not found: uv` | uv not installed / not on PATH | install uv, or use the plain `venv`+`pip` path |
| `mypy: Library stubs not installed for "yaml"` | `types-PyYAML` missing | it is in the `dev` extra: `uv sync --extra dev` |
| `pip` errors about `externally-managed-environment` | installing into system Python | create and activate a venv first (step 3) |
| `python3 --version` is < 3.11 | old system Python | install 3.11+ (step 2) |
| preflight exits 1 with `clob_v1_protocol` | live signing still on CLOB V1 | expected — stay on paper mode; see `docs/CLOB_V2.md` |

## Disclaimer

Educational/research software. Trading prediction markets carries real
financial risk. Validate with paper mode before committing capital.

## License

[MIT](LICENSE) © 2026 jablay46
