"""Tests for the scan runner and the runnable paper-trading path (task 0.5.13)."""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal

import pytest

from polymm.market import Market, OrderBook
from polymm.paper import PaperExchange
from polymm.pricing import Level
from polymm.runner import Bot
from polymm.strategy import ArbConfig

pytestmark = pytest.mark.unit


def D(x: str) -> Decimal:
    return Decimal(x)


def mk_book(token_id: str, asks, bids) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        bids=tuple(Level(D(p), D(s)) for p, s in bids),
        asks=tuple(Level(D(p), D(s)) for p, s in asks),
        tick_size=D("0.01"),
        min_order_size=D("1"),
    )


def mk_market(cid: str, y: str, n: str) -> Market:
    return Market(condition_id=cid, yes_token_id=y, no_token_id=n)


def arb_exchange() -> PaperExchange:
    ex = PaperExchange(balance=D("1000"), fee_rate=D("0.02"))
    ex.set_book(mk_book("Y", [("0.47", "500")], [("0.45", "500")]))
    ex.set_book(mk_book("N", [("0.49", "500")], [("0.47", "500")]))
    return ex


def test_scan_finds_and_executes_edge() -> None:
    from polymm.config import RiskConfig
    from polymm.risk import RiskLimits, RiskManager

    ex = arb_exchange()
    risk = RiskManager(RiskLimits.from_config(RiskConfig()), bankroll=D("1000"))
    bot = Bot(
        ex,
        risk,
        [mk_market("c", "Y", "N")],
        strategy_config=ArbConfig(fee_rate=D("0.02"), target_size=D("20")),
    )
    report = bot.scan_once()
    assert report.markets_scanned == 1
    assert report.intents_found == 1
    assert len(report.filled) == 1
    assert report.realized_edge_usd > 0
    assert not report.naked


def test_scan_ignores_fair_market() -> None:
    from polymm.config import RiskConfig
    from polymm.risk import RiskLimits, RiskManager

    ex = PaperExchange(balance=D("1000"))
    ex.set_book(mk_book("Y", [("0.52", "500")], [("0.50", "500")]))
    ex.set_book(mk_book("N", [("0.51", "500")], [("0.49", "500")]))
    risk = RiskManager(RiskLimits.from_config(RiskConfig()), bankroll=D("1000"))
    bot = Bot(ex, risk, [mk_market("c", "Y", "N")])
    report = bot.scan_once()
    assert report.intents_found == 0
    assert report.executions == []


def test_missing_book_is_skipped_not_fatal() -> None:
    from polymm.config import RiskConfig
    from polymm.risk import RiskLimits, RiskManager

    ex = PaperExchange(balance=D("1000"))  # no books registered
    risk = RiskManager(RiskLimits.from_config(RiskConfig()), bankroll=D("1000"))
    bot = Bot(ex, risk, [mk_market("c", "Y", "N")])
    report = bot.scan_once()
    assert report.skipped_errors == 1
    assert report.executions == []


def test_run_loops_without_real_sleep() -> None:
    from polymm.config import RiskConfig
    from polymm.risk import RiskLimits, RiskManager

    ex = arb_exchange()
    risk = RiskManager(RiskLimits.from_config(RiskConfig()), bankroll=D("1000"))
    bot = Bot(
        ex,
        risk,
        [mk_market("c", "Y", "N")],
        strategy_config=ArbConfig(fee_rate=D("0.02"), target_size=D("5")),
    )
    slept: list[float] = []
    report = bot.run(cycles=3, interval_secs=2.0, sleep=slept.append)
    assert report.markets_scanned == 3
    # sleeps only between cycles
    assert slept == [2.0, 2.0]


# ── the runnable entrypoint ───────────────────────────────────


def test_paper_run_module_executes_and_reports() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "polymm.paper_run", "--cycles", "1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "paper trading report" in proc.stdout
    assert "naked positions : 0" in proc.stdout


def test_paper_run_reports_positive_edge() -> None:
    from polymm.paper_run import build_paper_bot

    bot, exchange, _ = build_paper_bot()
    report = bot.scan_once()
    assert len(report.filled) == 1
    assert report.realized_edge_usd > 0
    assert not report.naked


def test_market_is_not_re_fired_after_a_fill() -> None:
    """A held complete set must not be re-bought every cycle (dedup)."""
    from polymm.config import RiskConfig
    from polymm.risk import RiskLimits, RiskManager

    ex = arb_exchange()
    risk = RiskManager(RiskLimits.from_config(RiskConfig()), bankroll=D("1000"))
    bot = Bot(
        ex,
        risk,
        [mk_market("c", "Y", "N")],
        strategy_config=ArbConfig(fee_rate=D("0.02"), target_size=D("5")),
    )
    first = bot.scan_once()
    assert len(first.filled) == 1
    second = bot.scan_once()
    assert second.intents_found == 0
    assert second.skipped_duplicates == 1
    assert second.executions == []


def test_market_resolving_soon_is_skipped() -> None:
    from datetime import UTC, datetime, timedelta

    from polymm.config import RiskConfig
    from polymm.risk import RiskLimits, RiskManager

    end = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    market = Market(condition_id="c", yes_token_id="Y", no_token_id="N", end_date=end)
    ex = arb_exchange()
    risk = RiskManager(RiskLimits.from_config(RiskConfig()), bankroll=D("1000"))
    bot = Bot(
        ex,
        risk,
        [market],
        strategy_config=ArbConfig(fee_rate=D("0.02"), target_size=D("5")),
        min_hours_to_resolution=1.0,
    )
    report = bot.scan_once()
    assert report.intents_found == 0
    assert report.executions == []


def test_market_not_accepting_orders_is_skipped() -> None:
    from polymm.config import RiskConfig
    from polymm.risk import RiskLimits, RiskManager

    market = Market(condition_id="c", yes_token_id="Y", no_token_id="N", accepting_orders=False)
    ex = arb_exchange()
    risk = RiskManager(RiskLimits.from_config(RiskConfig()), bankroll=D("1000"))
    bot = Bot(
        ex,
        risk,
        [market],
        strategy_config=ArbConfig(fee_rate=D("0.02"), target_size=D("5")),
    )
    assert bot.scan_once().intents_found == 0
