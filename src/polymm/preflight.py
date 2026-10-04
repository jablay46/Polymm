"""Preflight checks: refuse to go live unless everything is safe.

Run before every live session. The checks are deliberately paranoid and
fail-closed: any ``ERROR`` means the bot must not place a single live order.

Two classes of finding:

* ERROR — a hard stop. Missing signer, live gates not both permissive,
  nonsensical caps, mainnet address problems.
* WARNING — allowed but worth surfacing (e.g. tiny bankroll, no L2 creds
  which some endpoints require).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from polymm.chain.constants import get_contract_config, get_contract_config_v2
from polymm.config import Config


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    message: str


@dataclass
class PreflightReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """True only when there are no ERROR findings."""
        return not self.errors

    def add(self, severity: Severity, code: str, message: str) -> None:
        self.findings.append(Finding(severity, code, message))


def preflight(config: Config, *, bankroll_usd: Decimal | None = None) -> PreflightReport:
    """Validate a config before a live run. Never raises; returns findings."""
    report = PreflightReport()

    # 1. Dual-flag live gate.
    if not config.bot.enable_trading:
        report.add(Severity.ERROR, "trading_disabled", "enable_trading is false")
    if config.bot.mock_trading:
        report.add(Severity.ERROR, "mock_enabled", "mock_trading is true")
    if not config.bot.live_trading_allowed:
        report.add(
            Severity.ERROR,
            "live_gate",
            "live requires enable_trading=true AND mock_trading=false",
        )

    # 2. Credentials.
    if not config.credentials.has_signer():
        report.add(Severity.ERROR, "no_signer", "no private key for order signing")
    if not config.credentials.has_l2():
        report.add(
            Severity.WARNING,
            "no_l2",
            "no L2 (HMAC) credentials; authenticated endpoints may fail",
        )

    # 3. Exchange contracts for the configured chain.
    use_v2 = config.exchange.domain_version == "2"
    config_getter = get_contract_config_v2 if use_v2 else get_contract_config
    try:
        contracts = config_getter(config.exchange.chain_id, neg_risk=False)
    except Exception as exc:  # noqa: BLE001 - report, don't crash
        code = "no_v2_contracts" if use_v2 else "unknown_chain"
        report.add(Severity.ERROR, code, f"no contracts for chain: {exc}")
    else:
        # The configured addresses must match the known deployment for this
        # chain; otherwise a stale/hand-edited config silently points orders
        # at the wrong contract.
        if config.exchange.ctf_exchange_address.lower() != contracts.exchange.lower():
            report.add(
                Severity.ERROR,
                "exchange_address_mismatch",
                f"ctf_exchange_address {config.exchange.ctf_exchange_address} does not "
                f"match the known address {contracts.exchange} for chain "
                f"{config.exchange.chain_id} (domain_version="
                f"{config.exchange.domain_version})",
            )
        try:
            neg_contracts = config_getter(config.exchange.chain_id, neg_risk=True)
        except Exception:  # noqa: BLE001
            neg_contracts = None
        if (
            neg_contracts is not None
            and config.exchange.neg_risk_exchange_address.lower() != neg_contracts.exchange.lower()
        ):
            report.add(
                Severity.ERROR,
                "neg_risk_address_mismatch",
                f"neg_risk_exchange_address "
                f"{config.exchange.neg_risk_exchange_address} does not match the known "
                f"address {neg_contracts.exchange} for chain "
                f"{config.exchange.chain_id}",
            )

    # 3b. CLOB V2 protocol. The live path signs V1 orders (domain version
    # "1", V1 exchange addresses, USDC.e collateral). Polymarket retired V1
    # on 2026-04-28; V1-signed orders are rejected in production. Until the
    # adapter is migrated, going live is a hard stop — not a warning.
    if not use_v2:
        report.add(
            Severity.ERROR,
            "clob_v1_protocol",
            "live signing is still on CLOB V1 (domain version "
            f"{config.exchange.domain_version!r}); V2 requires version '2' and the V2 "
            "exchange contracts. Do not trade live until migrated.",
        )

    # 4. Risk sanity.
    r = config.risk
    if r.max_order_bankroll_fraction > r.max_total_bankroll_fraction:
        report.add(
            Severity.ERROR,
            "cap_order_gt_total",
            "per-order cap exceeds total exposure cap",
        )
    if r.max_total_bankroll_fraction > 1.0:
        report.add(Severity.ERROR, "cap_gt_bankroll", "total cap exceeds 100% of bankroll")
    if r.daily_loss_limit_usd <= 0:
        report.add(Severity.WARNING, "no_loss_limit", "daily loss limit is zero")
    if r.consecutive_trigger < 2:
        report.add(
            Severity.WARNING,
            "weak_burst_guard",
            "consecutive_trigger < 2 makes burst detection useless",
        )

    # 5. Small-capital awareness.
    if bankroll_usd is not None:
        if bankroll_usd < Decimal(str(r.min_bankroll_usd)):
            report.add(
                Severity.ERROR,
                "below_min_bankroll",
                f"bankroll {bankroll_usd} below min {r.min_bankroll_usd}",
            )
        if bankroll_usd < Decimal("100"):
            report.add(
                Severity.WARNING,
                "tiny_bankroll",
                f"bankroll {bankroll_usd} is small; fees/slippage dominate",
            )

    return report
