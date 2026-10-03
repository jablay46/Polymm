"""Command-line entrypoint.

    python -m polymm paper       # runnable paper trading (no keys/network)
    python -m polymm preflight   # safety checks against a config file

The ``preflight`` command exits non-zero when any ERROR finding is present,
so it can gate a deployment in CI or a shell script.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation

# Human-facing CLI: prints are the point.
# ruff: noqa: T201


def _cmd_paper(args: argparse.Namespace) -> int:
    from polymm.paper_run import _print_report, build_paper_bot

    bot, exchange, _ = build_paper_bot(
        bankroll=args.bankroll, fee_rate=args.fee_rate, target_size=args.target_size
    )
    report = bot.run(cycles=args.cycles, interval_secs=args.interval)
    _print_report(report, exchange)
    return 1 if report.naked else 0


def _cmd_preflight(args: argparse.Namespace) -> int:
    from polymm.config import load_config
    from polymm.preflight import Severity, preflight

    try:
        config = load_config(args.config)
    except Exception as exc:  # noqa: BLE001 - report cleanly, exit non-zero
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    bankroll = None
    if args.bankroll is not None:
        try:
            bankroll = Decimal(args.bankroll)
        except InvalidOperation:
            print(f"invalid bankroll: {args.bankroll}", file=sys.stderr)
            return 2

    report = preflight(config, bankroll_usd=bankroll)
    for finding in report.findings:
        marker = "ERROR" if finding.severity is Severity.ERROR else "WARN "
        print(f"[{marker}] {finding.code}: {finding.message}")
    if report.ok:
        print("preflight OK")
        return 0
    print(f"preflight FAILED ({len(report.errors)} error(s))", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="polymm", description="safety-first Polymarket bot")
    sub = parser.add_subparsers(dest="command", required=True)

    paper = sub.add_parser("paper", help="run paper trading (no keys, no network)")
    paper.add_argument("--cycles", type=int, default=1)
    paper.add_argument("--interval", type=float, default=1.0)
    paper.add_argument("--bankroll", default="200")
    paper.add_argument("--fee-rate", default="0.02")
    paper.add_argument("--target-size", default="20")
    paper.set_defaults(func=_cmd_paper)

    pre = sub.add_parser("preflight", help="check a config before going live")
    pre.add_argument("config", help="path to a YAML config")
    pre.add_argument("--bankroll", default=None)
    pre.set_defaults(func=_cmd_preflight)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
