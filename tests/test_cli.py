"""Tests for the CLI entrypoint (task 0.5.16)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from polymm.cli import main

pytestmark = pytest.mark.unit


def test_cli_paper_exit_zero(capsys) -> None:
    rc = main(["paper", "--cycles", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "paper trading report" in out


def test_cli_preflight_dry_run_config_fails(capsys) -> None:
    rc = main(["preflight", "config.example.yaml"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "live_gate" in captured.out
    assert "preflight FAILED" in captured.err


def test_cli_preflight_missing_config_returns_2() -> None:
    rc = main(["preflight", "does-not-exist.yaml"])
    assert rc == 2


def test_cli_preflight_bad_bankroll_returns_2() -> None:
    rc = main(["preflight", "config.example.yaml", "--bankroll", "not-a-number"])
    assert rc == 2


def test_python_m_polymm_paper_runs() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "polymm", "paper", "--cycles", "1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "naked positions : 0" in proc.stdout


def test_python_m_polymm_requires_subcommand() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "polymm"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
