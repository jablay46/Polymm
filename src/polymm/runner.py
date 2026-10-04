"""Scan loop: fetch books, find complete-set edges, execute them safely.

The runner is deliberately small and synchronous. It owns no strategy logic
and no execution logic — those live in :mod:`polymm.strategy` and
:mod:`polymm.execution`. Its only jobs are to iterate markets, translate
transport failures into skips (never into trades), and hand intents to the
executor.

``scan_once`` is the unit of work and is fully testable against a paper
venue. ``run`` just calls it on an interval.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from polymm.exchange import Exchange, ExchangeError
from polymm.execution import ExecutionConfig, ExecutionResult, execute_intent
from polymm.market import Market, OrderBook
from polymm.risk import RiskManager
from polymm.strategy import ArbConfig, evaluate_market


@dataclass
class ScanReport:
    markets_scanned: int = 0
    intents_found: int = 0
    skipped_errors: int = 0
    skipped_duplicates: int = 0
    halted: bool = False
    executions: list[ExecutionResult] = field(default_factory=list)

    @property
    def filled(self) -> list[ExecutionResult]:
        return [e for e in self.executions if e.success]

    @property
    def naked(self) -> list[ExecutionResult]:
        return [e for e in self.executions if e.holds_naked_position]

    @property
    def realized_edge_usd(self) -> float:
        return float(sum((e.realized_edge_usd for e in self.executions), start=0))


class Bot:
    def __init__(
        self,
        exchange: Exchange,
        risk: RiskManager,
        markets: Iterable[Market],
        *,
        strategy_config: ArbConfig | None = None,
        execution_config: ExecutionConfig | None = None,
        min_hours_to_resolution: float = 0.0,
        sync_bankroll: bool = False,
    ) -> None:
        self.exchange = exchange
        self.risk = risk
        self.markets = list(markets)
        self.strategy_config = strategy_config or ArbConfig()
        self.execution_config = execution_config or ExecutionConfig()
        # A market resolving within this window is skipped: an unhedged leg
        # could not be unwound before settlement.
        self.min_hours_to_resolution = min_hours_to_resolution
        # When true, refresh bankroll from the venue at the start of a scan.
        self.sync_bankroll = sync_bankroll
        # Markets we have already acted on, so the same arb is not re-fired
        # every cycle (complete-set positions are held to resolution).
        self._traded: set[str] = set()
        # Set when a scan ends with an unhedged position; blocks further scans.
        self._naked_pending = False

    def _books(self, market: Market) -> tuple[OrderBook, OrderBook] | None:
        try:
            yes = self.exchange.get_order_book(market.yes_token_id)
            no = self.exchange.get_order_book(market.no_token_id)
        except ExchangeError:
            return None
        return yes, no

    def _is_tradable(self, market: Market, now: datetime) -> bool:
        if not market.accepting_orders:
            return False
        if market.condition_id in self._traded:
            return False
        hours = market.hours_to_resolution(now)
        return hours is None or hours >= self.min_hours_to_resolution

    def scan_once(self) -> ScanReport:
        report = ScanReport()

        # A previous cycle left a naked position: stop and make it visible
        # rather than compounding exposure with more scans.
        if self.risk.state.open_positions and self._has_unresolved_naked():
            report.halted = True
            return report

        if self.sync_bankroll:
            import contextlib

            with contextlib.suppress(ExchangeError):
                self.risk.set_bankroll(self.exchange.balance_usdc())

        now = datetime.now(UTC)
        for market in self.markets:
            report.markets_scanned += 1
            if not self._is_tradable(market, now):
                if market.condition_id in self._traded:
                    report.skipped_duplicates += 1
                continue
            books = self._books(market)
            if books is None:
                report.skipped_errors += 1
                continue
            yes, no = books
            intent = evaluate_market(market, yes, no, self.strategy_config)
            if intent is None:
                continue
            report.intents_found += 1
            result = execute_intent(self.exchange, intent, self.risk, self.execution_config)
            report.executions.append(result)
            if result.success:
                self._traded.add(market.condition_id)
        return report

    def _has_unresolved_naked(self) -> bool:
        return bool(getattr(self, "_naked_pending", False))

    def run(
        self,
        *,
        cycles: int,
        interval_secs: float,
        sleep: Callable[[float], None] = time.sleep,
    ) -> ScanReport:
        total = ScanReport()
        for i in range(cycles):
            part = self.scan_once()
            total.markets_scanned += part.markets_scanned
            total.intents_found += part.intents_found
            total.skipped_errors += part.skipped_errors
            total.skipped_duplicates += part.skipped_duplicates
            total.halted = total.halted or part.halted
            total.executions.extend(part.executions)
            if part.naked:
                self._naked_pending = True
                total.halted = True
                break
            if part.halted:
                break
            if i < cycles - 1:
                sleep(interval_secs)
        return total
