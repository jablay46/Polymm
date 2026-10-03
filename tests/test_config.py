"""Tests for config loading and the trading safety gates (task 0.5.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from polymm.config import (
    ENV_API_SECRET,
    ENV_ENABLE_TRADING,
    ENV_MOCK_TRADING,
    ENV_PRIVATE_KEY,
    REDACTED,
    BotConfig,
    Credentials,
    load_config,
)

pytestmark = pytest.mark.unit


# ── Dual-flag live gate ───────────────────────────────────────


def test_defaults_are_dry_run() -> None:
    """A bare BotConfig must never permit live trading."""
    bot = BotConfig()
    assert bot.enable_trading is False
    assert bot.mock_trading is True
    assert bot.live_trading_allowed is False


@pytest.mark.parametrize(
    ("enable", "mock", "expected"),
    [
        (False, True, False),  # both safe
        (False, False, False),  # enable off — still dry
        (True, True, False),  # mock on — still dry
        (True, False, True),  # both permissive — the only live path
    ],
)
def test_live_requires_both_flags(enable: bool, mock: bool, expected: bool) -> None:
    bot = BotConfig(enable_trading=enable, mock_trading=mock)
    assert bot.live_trading_allowed is expected


# ── Fail-closed behaviour ─────────────────────────────────────


def test_unknown_key_is_rejected() -> None:
    """A typo must fail loudly, not silently disable a safety setting."""
    with pytest.raises(ValidationError):
        BotConfig(enable_trading=True, mock_traiding=False)  # deliberate typo


def test_missing_exchange_is_rejected(tmp_path) -> None:
    """A config without the exchange section must be rejected (fail-closed)."""
    p = tmp_path / "config.yaml"
    p.write_text("bot:\n  enable_trading: false\n")
    with pytest.raises(ValidationError):
        load_config(p)


def test_malformed_address_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config_dict(
            {
                "exchange": {
                    "ctf_exchange_address": "not-an-address",
                    "neg_risk_exchange_address": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
                }
            }
        )


def test_out_of_range_fraction_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config_dict(_valid_exchange({"risk": {"max_order_bankroll_fraction": 1.5}}))


def test_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_config_root_must_be_mapping(tmp_path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("- just\n- a\n- list\n")
    with pytest.raises(ValueError):
        load_config(p)


# ── Env overrides ─────────────────────────────────────────────


def test_env_can_enable_live(tmp_path) -> None:
    path = _write_config(tmp_path, _valid_exchange({}))
    cfg = load_config(
        path,
        env={ENV_ENABLE_TRADING: "true", ENV_MOCK_TRADING: "false"},
    )
    assert cfg.bot.live_trading_allowed is True


def test_env_default_keeps_dry_run(tmp_path) -> None:
    path = _write_config(tmp_path, _valid_exchange({}))
    cfg = load_config(path, env={})
    assert cfg.bot.live_trading_allowed is False


# ── Credentials never serialise ───────────────────────────────


def test_credentials_repr_is_redacted() -> None:
    creds = Credentials(
        private_key="0x" + "ab" * 32,
        api_key="key-123",
        api_secret="secret-456",
        api_passphrase="pass-789",
    )
    text = repr(creds)
    assert "ab" * 32 not in text
    assert "secret-456" not in text
    assert "pass-789" not in text
    assert text.count(REDACTED) == 4


def test_credentials_come_from_env_only(tmp_path) -> None:
    path = _write_config(tmp_path, _valid_exchange({}))
    cfg = load_config(path, env={ENV_PRIVATE_KEY: "0x" + "cd" * 32, ENV_API_SECRET: "s" * 20})
    assert cfg.credentials.private_key == "0x" + "cd" * 32
    assert cfg.credentials.has_signer() is True
    # api_key absent => no complete L2 triple
    assert cfg.credentials.has_l2() is False


# ── helpers ───────────────────────────────────────────────────


def _valid_exchange(extra: dict) -> dict:
    base = {
        "exchange": {
            "ctf_exchange_address": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
            "neg_risk_exchange_address": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
        }
    }
    base.update(extra)
    return base


def _write_config(tmp_path, data: dict):
    import yaml

    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def load_config_dict(data: dict):
    """Validate an in-memory mapping without touching the filesystem."""
    from polymm.config import Config, ExchangeConfig, load_credentials

    return Config(
        exchange=ExchangeConfig(**data["exchange"]),
        credentials=load_credentials({}),
        **{k: v for k, v in data.items() if k != "exchange"},
    )
