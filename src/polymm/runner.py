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
    ) -> None:
        self.exchange = exchange
        self.risk = risk
        self.markets = list(markets)
        self.strategy_config = strategy_config or ArbConfig()
        self.execution_config = execution_config or ExecutionConfig()

    def _books(self, market: Market) -> tuple[OrderBook, OrderBook] | None:
        try:
            yes = self.exchange.get_order_book(market.yes_token_id)
            no = self.exchange.get_order_book(market.no_token_id)
        except ExchangeError:
            return None
        return yes, no

    def scan_once(self) -> ScanReport:
        report = ScanReport()
        for market in self.markets:
            report.markets_scanned += 1
            books = self._books(market)
            if books is None:
                report.skipped_errors += 1
                continue
            yes, no = books
            intent = evaluate_market(market, yes, no, self.strategy_config)
            if intent is None:
                continue
            report.intents_found += 1
            report.executions.append(
                execute_intent(self.exchange, intent, self.risk, self.execution_config)
            )
        return report

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
            total.executions.extend(part.executions)
            if i < cycles - 1:
                sleep(interval_secs)
        return total
