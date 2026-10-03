"""Pre-trade risk checks and a kill switch.

Every order must pass :meth:`RiskManager.evaluate_order` before it can be
signed. The manager is the last line of defence: it is the only place that
knows the account's bankroll, exposure, and loss so far, and it fails
closed — an unknown value or an unreadable state denies the trade.

Two independent safety mechanisms:

* Hard caps — per-order notional, total exposure, open positions, minimum
  bankroll, minimum book depth. These are fractions of bankroll, so a small
  account scales down automatically.
* A kill switch — trips on the daily loss limit, or on a burst of
  ``consecutive_trigger`` large trades inside ``sequence_window_secs``
  (the signature of a flash move or a fat-finger loop). Once tripped, all
  orders are denied until ``trip_duration_secs`` have elapsed.

Time is injected so behaviour is deterministic in tests.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from polymm.config import RiskConfig

ZERO = Decimal("0")


@dataclass(frozen=True)
class Decision:
    """Outcome of a pre-trade check."""

    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


@dataclass(frozen=True)
class RiskLimits:
    max_order_notional: Decimal
    max_total_exposure: Decimal
    max_open_positions: int
    min_bankroll: Decimal
    daily_loss_limit: Decimal
    large_trade_shares: Decimal
    consecutive_trigger: int
    sequence_window_secs: float
    min_depth_usd: Decimal
    trip_duration_secs: float

    @classmethod
    def from_config(cls, cfg: RiskConfig) -> RiskLimits:
        return cls(
            max_order_notional=Decimal(str(cfg.max_order_bankroll_fraction)),
            max_total_exposure=Decimal(str(cfg.max_total_bankroll_fraction)),
            max_open_positions=cfg.max_open_positions,
            min_bankroll=Decimal(str(cfg.min_bankroll_usd)),
            daily_loss_limit=Decimal(str(cfg.daily_loss_limit_usd)),
            large_trade_shares=Decimal(str(cfg.large_trade_shares)),
            consecutive_trigger=cfg.consecutive_trigger,
            sequence_window_secs=float(cfg.sequence_window_secs),
            min_depth_usd=Decimal(str(cfg.min_depth_usd)),
            trip_duration_secs=float(cfg.trip_duration_secs),
        )


@dataclass
class RiskState:
    bankroll: Decimal
    realized_pnl: Decimal = ZERO
    daily_pnl: Decimal = ZERO
    daily_date: date | None = None
    open_positions: int = 0
    exposure: Decimal = ZERO
    large_trade_times: deque[float] = field(default_factory=deque)
    kill_until: float | None = None


class RiskManager:
    """Stateful risk gate. One instance per trading account."""

    def __init__(
        self,
        limits: RiskLimits,
        *,
        bankroll: Decimal | float | int,
        clock: Callable[[], float] | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        import time

        self.limits = limits
        self._clock = clock if clock is not None else time.monotonic
        self._today = today if today is not None else date.today
        bankroll_dec = Decimal(str(bankroll))
        if bankroll_dec < ZERO:
            raise ValueError(f"bankroll must be >= 0, got {bankroll_dec}")
        self.state = RiskState(bankroll=bankroll_dec, daily_date=self._today())

    # ── kill switch ───────────────────────────────────────────

    def is_halted(self) -> bool:
        until = self.state.kill_until
        if until is None:
            return False
        if self._clock() >= until:
            self.state.kill_until = None
            return False
        return True

    def trip(self, reason: str) -> None:
        """Trip the kill switch for ``trip_duration_secs``."""
        self.state.kill_until = self._clock() + self.limits.trip_duration_secs
        self.last_trip_reason = reason

    last_trip_reason: str = ""

    def reset_kill_switch(self) -> None:
        self.state.kill_until = None

    # ── state updates ─────────────────────────────────────────

    def record_fill(self, *, notional: Decimal, open_position: bool) -> None:
        self.state.exposure += notional
        if open_position:
            self.state.open_positions += 1

    def record_close(self, *, notional: Decimal, pnl: Decimal) -> None:
        self.state.exposure = max(ZERO, self.state.exposure - notional)
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self.state.realized_pnl += pnl
        self._roll_day_if_needed()
        self.state.daily_pnl += pnl
        if self.state.daily_pnl <= -self.limits.daily_loss_limit:
            self.trip("daily loss limit reached")

    def _roll_day_if_needed(self) -> None:
        today = self._today()
        if self.state.daily_date != today:
            self.state.daily_date = today
            self.state.daily_pnl = ZERO

    def set_bankroll(self, bankroll: Decimal | float | int) -> None:
        self.state.bankroll = Decimal(str(bankroll))

    def _prune_large_trades(self, now: float) -> None:
        cutoff = now - self.limits.sequence_window_secs
        while self.state.large_trade_times and self.state.large_trade_times[0] < cutoff:
            self.state.large_trade_times.popleft()

    # ── the gate ──────────────────────────────────────────────

    def evaluate_order(
        self,
        *,
        notional: Decimal | float | int,
        size_shares: Decimal | float | int,
        depth_usd: Decimal | float | int,
    ) -> Decision:
        """Approve or deny an order. Denies whenever anything is unknown."""
        now = self._clock()

        if self.is_halted():
            return Decision(False, f"kill switch active: {self.last_trip_reason}")

        bankroll = self.state.bankroll
        if bankroll < self.limits.min_bankroll:
            return Decision(
                False,
                f"bankroll {bankroll} below minimum {self.limits.min_bankroll}",
            )

        order_notional = Decimal(str(notional))
        if order_notional <= ZERO:
            return Decision(False, "order notional must be positive")

        max_order = bankroll * self.limits.max_order_notional
        if order_notional > max_order:
            return Decision(
                False,
                f"order notional {order_notional} exceeds cap {max_order}",
            )

        max_total = bankroll * self.limits.max_total_exposure
        if self.state.exposure + order_notional > max_total:
            return Decision(
                False,
                f"total exposure would exceed cap {max_total}",
            )

        if self.state.open_positions >= self.limits.max_open_positions:
            return Decision(
                False,
                f"open positions {self.state.open_positions} at cap "
                f"{self.limits.max_open_positions}",
            )

        depth = Decimal(str(depth_usd))
        if depth < self.limits.min_depth_usd:
            return Decision(
                False,
                f"book depth {depth} below minimum {self.limits.min_depth_usd}",
            )

        shares = Decimal(str(size_shares))
        if shares >= self.limits.large_trade_shares:
            self._prune_large_trades(now)
            self.state.large_trade_times.append(now)
            if len(self.state.large_trade_times) >= self.limits.consecutive_trigger:
                self.trip("too many large trades in window")
                return Decision(False, "large-trade burst tripped kill switch")

        return Decision(True)
