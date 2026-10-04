"""Configuration loading and safety gates.

Design rules (non-negotiable):

1. **Fail-closed.** Every trading gate defaults to the safe value. A missing
   or malformed config stops the bot rather than starting it half-configured.
2. **Dual-flag live.** Live orders require ``enable_trading=True`` *and*
   ``mock_trading=False``. Any other combination is dry-run.
3. **Secrets stay in the environment.** :class:`Credentials` is never
   serialised back out and its ``repr`` is redacted.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

REDACTED = "<redacted>"

# Values accepted as boolean True/False from the environment. Anything else
# (including empty string and near-misses like "y", "t", "flase") is a hard
# error: silently treating a typo as False is how a live gate fails open.
_ENV_TRUE = {"1", "true", "yes", "on"}
_ENV_FALSE = {"0", "false", "no", "off"}

# Environment variable names for credentials. Kept in one place so the
# redaction tests and the loader cannot drift apart.
ENV_PRIVATE_KEY = "POLYMM_PRIVATE_KEY"
ENV_FUNDER_ADDRESS = "POLYMM_FUNDER_ADDRESS"
ENV_API_KEY = "POLYMM_API_KEY"  # noqa: S105 — env var *name*, not a secret
ENV_API_SECRET = "POLYMM_API_SECRET"  # noqa: S105 — env var *name*, not a secret
ENV_API_PASSPHRASE = "POLYMM_API_PASSPHRASE"  # noqa: S105 — env var *name*, not a secret
ENV_ENABLE_TRADING = "POLYMM_ENABLE_TRADING"
ENV_MOCK_TRADING = "POLYMM_MOCK_TRADING"


class _Base(BaseModel):
    """Strict base: reject unknown keys so typos fail loudly, not silently."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class BotConfig(_Base):
    """Top-level trading gates.

    ``enable_trading`` and ``mock_trading`` are deliberately independent:
    both must be permissive for a live order to leave the process.
    """

    enable_trading: bool = False
    mock_trading: bool = True
    wallets_to_track: list[str] = Field(default_factory=list)

    @property
    def live_trading_allowed(self) -> bool:
        """True only when both gates are explicitly permissive."""
        return self.enable_trading and not self.mock_trading


class TestnetConfig(_Base):
    enabled: bool = False
    chain_id: int = 80002


class SiteConfig(_Base):
    gamma_api_base: str = "https://gamma-api.polymarket.com"
    clob_api_base: str = "https://clob.polymarket.com"
    polygon_ws_url: str = "wss://polygon-rpc.com"
    testnet: TestnetConfig = Field(default_factory=TestnetConfig)


class ExchangeConfig(_Base):
    chain_id: int = 137
    ctf_exchange_address: str
    neg_risk_exchange_address: str
    domain_name: str = "Polymarket CTF Exchange"
    domain_version: str = "1"

    @field_validator("ctf_exchange_address", "neg_risk_exchange_address")
    @classmethod
    def _valid_address(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("0x") and len(v) == 42):
            raise ValueError(f"not a 20-byte hex address: {v!r}")
        int(v, 16)  # raises ValueError if non-hex
        return v


class RiskConfig(_Base):
    """Caps expressed as fractions of bankroll so a small account scales down."""

    max_order_bankroll_fraction: float = Field(default=0.05, gt=0.0, le=1.0)
    max_total_bankroll_fraction: float = Field(default=0.50, gt=0.0, le=1.0)
    max_open_positions: int = Field(default=4, ge=1)
    min_bankroll_usd: float = Field(default=50.0, ge=0.0)
    daily_loss_limit_usd: float = Field(default=25.0, ge=0.0)
    large_trade_shares: float = Field(default=1000.0, ge=0.0)
    consecutive_trigger: int = Field(default=3, ge=1)
    sequence_window_secs: int = Field(default=30, ge=1)
    min_depth_usd: float = Field(default=200.0, ge=0.0)
    trip_duration_secs: int = Field(default=60, ge=1)


class StrategyConfig(_Base):
    complete_set_min_edge: float = Field(default=0.01, ge=0.0, lt=1.0)
    max_basket_cost: float = Field(default=0.99, gt=0.0, le=1.0)
    max_leg_price: float = Field(default=0.985, gt=0.0, le=1.0)
    max_spread: float = Field(default=0.08, ge=0.0, le=1.0)
    fee_rate: float = Field(default=0.02, ge=0.0, le=1.0)
    target_size: float = Field(default=100.0, gt=0.0)
    min_visible_size: float = Field(default=1.0, ge=0.0)
    improve_ticks: int = Field(default=1, ge=0)
    max_skew_ticks: int = Field(default=1, ge=0)
    imbalance_shares_for_max_skew: float = Field(default=200.0, gt=0.0)
    reward_band_enabled: bool = True
    taker_mode_enabled: bool = False
    min_replace_ticks: int = Field(default=1, ge=1)


class LoggingConfig(_Base):
    level: str = "INFO"


class Credentials(BaseModel):
    """Wallet/API secrets.

    Held only in memory, sourced from the environment, never serialised.
    Values are :class:`~pydantic.SecretStr`, so ``model_dump()`` and
    ``model_dump_json()`` emit ``**********`` rather than the secret; ``repr``
    is additionally redacted so an accidental log or exception cannot leak a
    key.
    """

    model_config = ConfigDict(frozen=True)

    private_key: SecretStr | None = None
    funder_address: str | None = None
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None
    api_passphrase: SecretStr | None = None

    def __repr__(self) -> str:
        return (
            "Credentials("
            f"private_key={REDACTED if self.private_key else None}, "
            f"funder_address={self.funder_address}, "
            f"api_key={REDACTED if self.api_key else None}, "
            f"api_secret={REDACTED if self.api_secret else None}, "
            f"api_passphrase={REDACTED if self.api_passphrase else None})"
        )

    __str__ = __repr__

    def has_l2(self) -> bool:
        """True when a full L2 (HMAC) credential triple is present."""
        return bool(self.api_key and self.api_secret and self.api_passphrase)

    def has_signer(self) -> bool:
        """True when an L1 signing key is present."""
        return bool(self.private_key)


class Config(BaseModel):
    """The whole validated configuration tree."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bot: BotConfig = Field(default_factory=BotConfig)
    site: SiteConfig = Field(default_factory=SiteConfig)
    exchange: ExchangeConfig
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    credentials: Credentials = Field(default_factory=Credentials)


def _env_bool_in(src: Mapping[str, str], name: str) -> bool | None:
    """Parse a boolean from ``src[name]``.

    Returns ``None`` when the variable is unset *or empty* (an empty value
    is treated as "not configured", so ``NAME=`` in a .env file cannot
    silently flip a gate). Any non-empty value that is not a recognised
    boolean raises :class:`ValueError` — fail closed, never guess.
    """
    raw = src.get(name)
    if raw is None:
        return None
    token = raw.strip().lower()
    if token == "":
        return None
    if token in _ENV_TRUE:
        return True
    if token in _ENV_FALSE:
        return False
    raise ValueError(
        f"{name}={raw!r} is not a recognised boolean (use one of: {sorted(_ENV_TRUE | _ENV_FALSE)})"
    )


def load_credentials(env: Mapping[str, str] | None = None) -> Credentials:
    """Read credentials from the environment (or an injected mapping)."""
    src = os.environ if env is None else env

    def secret(name: str) -> SecretStr | None:
        value = src.get(name)
        return SecretStr(value) if value else None

    return Credentials(
        private_key=secret(ENV_PRIVATE_KEY),
        funder_address=src.get(ENV_FUNDER_ADDRESS) or None,
        api_key=secret(ENV_API_KEY),
        api_secret=secret(ENV_API_SECRET),
        api_passphrase=secret(ENV_API_PASSPHRASE),
    )


def _apply_env_gates(bot: BotConfig, env: dict[str, str] | None) -> BotConfig:
    """Let env override the trading gates (useful for CI and containers)."""
    src = os.environ if env is None else env
    enable = _env_bool_in(src, ENV_ENABLE_TRADING)
    mock = _env_bool_in(src, ENV_MOCK_TRADING)
    if enable is None and mock is None:
        return bot
    return bot.model_copy(
        update={
            "enable_trading": bot.enable_trading if enable is None else enable,
            "mock_trading": bot.mock_trading if mock is None else mock,
        }
    )


def load_config(
    path: str | Path | None = None,
    *,
    env: dict[str, str] | None = None,
) -> Config:
    """Load, validate, and gate-check configuration.

    Raises:
        FileNotFoundError: the config path does not exist.
        ValueError: an env gate value is not a recognised boolean.
        pydantic.ValidationError: the config is malformed (fail-closed).
    """
    raw: dict[str, Any] = {}
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"config not found: {p}")
        loaded = yaml.safe_load(p.read_text()) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"config root must be a mapping, got {type(loaded).__name__}")
        raw = loaded

    # Credentials come from the environment only. A `credentials:` block in
    # YAML is ignored by design, so say so loudly rather than silently
    # dropping what the operator may believe is the source of truth.
    if "credentials" in raw:
        import warnings

        warnings.warn(
            "config key 'credentials' is ignored; secrets are read from the "
            "environment only (POLYMM_PRIVATE_KEY, POLYMM_API_*).",
            stacklevel=2,
        )

    bot_raw = raw.get("bot") or {}
    if not isinstance(bot_raw, dict):
        raise ValueError(f"config 'bot' must be a mapping, got {type(bot_raw).__name__}")
    bot = BotConfig(**bot_raw)
    bot = _apply_env_gates(bot, env)

    # Build from the raw mapping so pydantic validates every section and
    # reports missing/invalid fields as a ValidationError (fail-closed)
    # rather than a raw KeyError.
    body = {k: v for k, v in raw.items() if k != "credentials"}
    body["bot"] = bot
    return Config(credentials=load_credentials(env), **body)
