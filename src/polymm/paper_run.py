"""Runnable paper-trading path: no keys, no network, real code.

Run it::

    python -m polymm.paper_run --cycles 3

It builds a couple of synthetic binary markets (one with a clear
complete-set edge, one without), runs the real strategy/risk/execution
pipeline against an in-memory :class:`~polymm.paper.PaperExchange`, and
prints a report. This is the end-to-end smoke test a newcomer runs first,
and the harness the integration tests drive.
"""

from __future__ import annotations

# This module is a human-facing CLI: prints are its whole purpose.
# ruff: noqa: T201, S106 - CLI prints; token ids are not secrets
import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal

from polymm.config import RiskConfig
from polymm.market import Market, OrderBook
from polymm.paper import PaperExchange
from polymm.pricing import Level
from polymm.risk import RiskLimits, RiskManager
from polymm.runner import Bot, ScanReport
from polymm.strategy import ArbConfig

D = Decimal


def _book(token_id: str, asks: list[tuple[str, str]], bids: list[tuple[str, str]]) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        bids=tuple(Level(D(p), D(s)) for p, s in bids),
        asks=tuple(Level(D(p), D(s)) for p, s in asks),
        tick_size=D("0.01"),
        min_order_size=D("1"),
    )


@dataclass(frozen=True)
class Scenario:
    markets: list[Market]
    books: dict[str, OrderBook]


def demo_scenario() -> Scenario:
    """Two markets: one with a ~3c complete-set edge, one fairly priced."""
    arb = Market(condition_id="0xarb", yes_token_id="ARB-Y", no_token_id="ARB-N")
    fair = Market(condition_id="0xfair", yes_token_id="FAIR-Y", no_token_id="FAIR-N")
    books = {
        "ARB-Y": _book("ARB-Y", [("0.47", "500")], [("0.45", "500")]),
        "ARB-N": _book("ARB-N", [("0.49", "500")], [("0.47", "500")]),
        "FAIR-Y": _book("FAIR-Y", [("0.52", "500")], [("0.50", "500")]),
        "FAIR-N": _book("FAIR-N", [("0.51", "500")], [("0.49", "500")]),
    }
    return Scenario(markets=[arb, fair], books=books)


def build_paper_bot(
    *,
    bankroll: str = "200",
    fee_rate: str = "0.02",
    target_size: str = "10",
) -> tuple[Bot, PaperExchange, Scenario]:
    scenario = demo_scenario()
    exchange = PaperExchange(balance=D(bankroll), fee_rate=D(fee_rate))
    for book in scenario.books.values():
        exchange.set_book(book)

    risk = RiskManager(
        RiskLimits.from_config(RiskConfig()),
        bankroll=D(bankroll),
    )
    strategy = ArbConfig(fee_rate=D(fee_rate), target_size=D(target_size))
    bot = Bot(exchange, risk, scenario.markets, strategy_config=strategy)
    return bot, exchange, scenario


def _print_report(report: ScanReport, exchange: PaperExchange) -> None:
    print("── paper trading report ─────────────────────────")
    print(f"markets scanned : {report.markets_scanned}")
    print(f"intents found   : {report.intents_found}")
    print(f"skipped (errors): {report.skipped_errors}")
    print(f"fills           : {len(report.filled)}")
    print(f"naked positions : {len(report.naked)}")
    print(f"realized edge   : ${report.realized_edge_usd:.4f}")
    print(f"cash remaining  : ${exchange.balance_usdc():.4f}")
    if report.naked:
        print("WARNING: unhedged positions detected", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="polymm paper-trading smoke run")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--bankroll", default="200")
    parser.add_argument("--fee-rate", default="0.02")
    parser.add_argument("--target-size", default="10")
    args = parser.parse_args(argv)

    bot, exchange, _ = build_paper_bot(
        bankroll=args.bankroll, fee_rate=args.fee_rate, target_size=args.target_size
    )
    report = bot.run(cycles=args.cycles, interval_secs=args.interval)
    _print_report(report, exchange)
    return 1 if report.naked else 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess test
    raise SystemExit(main())
