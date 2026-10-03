"""polymm — safety-first Polymarket market-making + complete-set arbitrage bot."""

from polymm.config import (
    BotConfig,
    Credentials,
    ExchangeConfig,
    LoggingConfig,
    RiskConfig,
    SiteConfig,
    StrategyConfig,
    load_config,
)

__all__ = [
    "BotConfig",
    "Credentials",
    "ExchangeConfig",
    "LoggingConfig",
    "RiskConfig",
    "SiteConfig",
    "StrategyConfig",
    "load_config",
]

__version__ = "0.1.0"
