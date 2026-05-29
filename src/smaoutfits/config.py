"""Typed, validated configuration loading.

The YAML files under ``config/`` are the single source of truth for what we
trade and how. This module parses them into validated pydantic models so a
typo or a missing field fails loudly at startup rather than mid-trade.

API keys are NEVER read from YAML. They are resolved from environment variables
(optionally loaded from a ``.env`` file) using the ``*_env`` names in the
config, so secrets stay out of the repo.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

try:  # optional convenience; .env is not required
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs) -> bool:  # type: ignore[misc]
        return False

__all__ = [
    "AppConfig",
    "RiskConfig",
    "OutfitsConfig",
    "Universe",
    "Outfit",
    "System",
    "load_app_config",
    "load_risk_config",
    "load_outfits",
    "load_universe",
    "resolve_credentials",
]

Mode = Literal["backtest", "paper", "live"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# config.yaml
# --------------------------------------------------------------------------- #
class ExchangeCfg(_Base):
    name: str
    api_key_env: str
    api_secret_env: str


class DataCfg(_Base):
    cache_dir: str = "data/cache"
    default_timeframe: str = "1h"
    history_days: int = 730


class BacktestCfg(_Base):
    initial_cash: float = Field(10_000, gt=0)
    fee_pct: float = Field(0.0026, ge=0, le=0.1)
    slippage_pct: float = Field(0.0005, ge=0, le=0.1)


class LiveCfg(_Base):
    confirm: bool = False


class AppConfig(_Base):
    mode: Mode = "backtest"
    exchange: ExchangeCfg
    data: DataCfg = Field(default_factory=DataCfg)
    universe_file: str = "config/universe.yaml"
    outfits_file: str = "config/outfits.yaml"
    risk_file: str = "config/risk.yaml"
    backtest: BacktestCfg = Field(default_factory=BacktestCfg)
    live: LiveCfg = Field(default_factory=LiveCfg)

    def live_orders_allowed(self) -> bool:
        """Real orders are allowed only when explicitly in live mode AND the
        live.confirm safety flag has been deliberately set to true."""
        return self.mode == "live" and self.live.confirm


# --------------------------------------------------------------------------- #
# risk.yaml
# --------------------------------------------------------------------------- #
class PositionCfg(_Base):
    sizing: Literal["risk_based", "fixed_fraction"] = "risk_based"
    risk_per_trade_pct: float = Field(0.0075, gt=0, le=1)   # fraction of equity risked per trade
    max_position_pct: float = Field(0.10, gt=0, le=1)       # cap a single position's fraction
    min_position_quote: float = Field(25.0, ge=0)           # skip trades smaller than this


class StopsCfg(_Base):
    require_stop_on_entry: bool = True       # never enter without a stop
    method: Literal["atr", "percent"] = "atr"
    stop_loss_pct: float = Field(0.05, gt=0, le=1)          # used when method == percent
    atr_period: int = Field(14, ge=1)
    atr_stop_mult: float = Field(2.5, gt=0)                 # stop = entry - atr_stop_mult * ATR


class PortfolioCfg(_Base):
    max_gross_exposure_pct: float = Field(0.40, gt=0, le=1)
    max_open_positions: int = Field(4, ge=1)
    max_positions_per_asset: int = Field(1, ge=1)
    reserve_cash_pct: float = Field(0.10, ge=0, le=1)       # always keep this fraction in cash


class KillSwitchCfg(_Base):
    max_daily_loss_pct: float = Field(0.04, gt=0, le=1)
    max_drawdown_pct: float = Field(0.15, gt=0, le=1)
    max_consecutive_losses: int = Field(5, ge=1)
    state_file: str = "data/kill_switch_state.json"


class GuardsCfg(_Base):
    min_seconds_between_orders: float = Field(5.0, ge=0)
    max_order_notional_quote: float = Field(1000.0, gt=0)
    max_slippage_pct: float = Field(0.01, gt=0, le=1)       # reject live fills past this deviation


class RiskConfig(_Base):
    position: PositionCfg = Field(default_factory=PositionCfg)
    stops: StopsCfg = Field(default_factory=StopsCfg)
    portfolio: PortfolioCfg = Field(default_factory=PortfolioCfg)
    kill_switch: KillSwitchCfg = Field(default_factory=KillSwitchCfg)
    guards: GuardsCfg = Field(default_factory=GuardsCfg)


# --------------------------------------------------------------------------- #
# outfits.yaml
# --------------------------------------------------------------------------- #
class Outfit(_Base):
    id: str
    periods: list[int]
    label: str
    note: str | None = None
    anchor: int | None = None
    numerology: bool = False


class System(_Base):
    id: str
    instrument: str
    outfit_id: str
    timeframes: list[str]
    trend_fast: int
    trend_slow: int
    key_level: int
    high_vol_level: int


class OutfitsConfig(_Base):
    outfits: list[Outfit]
    systems: list[System] = Field(default_factory=list)

    def by_id(self, outfit_id: str) -> Outfit:
        for o in self.outfits:
            if o.id == outfit_id:
                return o
        raise KeyError(f"no outfit with id {outfit_id!r}")


# --------------------------------------------------------------------------- #
# universe.yaml
# --------------------------------------------------------------------------- #
class Universe(_Base):
    crypto_kraken: list[str] = Field(default_factory=list)
    # Equities are a free-form nested dict (indices/etfs/etc.) used by the
    # Webull adapter later; we don't constrain its shape here.
    equities_webull: dict[str, list[str]] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def _read_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"expected a mapping at top of {p}, got {type(data).__name__}")
    return data


def load_app_config(path: str | Path = "config/config.yaml") -> AppConfig:
    return AppConfig.model_validate(_read_yaml(path))


def load_risk_config(path: str | Path = "config/risk.yaml") -> RiskConfig:
    return RiskConfig.model_validate(_read_yaml(path))


def load_outfits(path: str | Path = "config/outfits.yaml") -> OutfitsConfig:
    return OutfitsConfig.model_validate(_read_yaml(path))


def load_universe(path: str | Path = "config/universe.yaml") -> Universe:
    return Universe.model_validate(_read_yaml(path))


def resolve_credentials(exchange: ExchangeCfg) -> tuple[str | None, str | None]:
    """Resolve (api_key, api_secret) from the environment.

    Loads a ``.env`` if present. Returns ``(None, None)`` when unset — callers
    in backtest/paper mode don't need credentials and should tolerate this.
    """
    load_dotenv()
    return os.environ.get(exchange.api_key_env), os.environ.get(exchange.api_secret_env)
