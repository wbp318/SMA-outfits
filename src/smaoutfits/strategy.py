"""Strategies: turn OHLCV into a target position series.

A strategy is a deterministic, broker-agnostic function of a price frame. For
spot crypto we model LONG/FLAT only (no shorting) — the target position is 1.0
(fully long) or 0.0 (in cash). The backtester applies the no-lookahead shift and
the costs; strategies never see fees, balances, or the broker.

Returning a target *position* (rather than discrete buy/sell signals) keeps the
backtest, paper, and live paths consuming one shape, and makes "what would this
do right now" a single lookup of the last value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from .config import Outfit, System
from .indicators import regime, sma

__all__ = [
    "Strategy",
    "CrossoverStrategy",
    "OutfitStrategy",
    "StackedOutfitStrategy",
    "SystemsStrategy",
    "build_outfit_strategy",
]


class Strategy(Protocol):
    name: str

    def warmup_bars(self) -> int:
        """Bars of history needed before any signal is valid."""
        ...

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        """Return a 0.0/1.0 (flat/long) series aligned to ``df.index``."""
        ...


def _long_flat(condition: pd.Series, index: pd.Index) -> pd.Series:
    out = pd.Series(0.0, index=index, dtype="float64")
    out[condition.fillna(False)] = 1.0
    return out


@dataclass
class CrossoverStrategy:
    """Long while the fast SMA is above the slow SMA, else flat."""

    fast: int
    slow: int
    name: str = "crossover"

    def __post_init__(self) -> None:
        if self.fast >= self.slow:
            raise ValueError(f"fast ({self.fast}) must be < slow ({self.slow})")

    def warmup_bars(self) -> int:
        return self.slow

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        return _long_flat(regime(df["close"], self.fast, self.slow) > 0, df.index)


@dataclass
class OutfitStrategy:
    """Trade an outfit's period set as a single fast/slow crossover.

    ``fast_idx`` / ``slow_idx`` index into the (sorted) period list; the default
    pairs the shortest against the longest period — the most common multi-MA
    trend interpretation. This is the consistent rule used to rank all outfits.
    """

    periods: list[int]
    fast_idx: int = 0
    slow_idx: int = -1
    name: str = "outfit"

    def __post_init__(self) -> None:
        self._periods = sorted(self.periods)
        self._fast = self._periods[self.fast_idx]
        self._slow = self._periods[self.slow_idx]
        if self._fast >= self._slow:
            raise ValueError(f"resolved fast {self._fast} !< slow {self._slow}")

    def warmup_bars(self) -> int:
        return self._slow

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        return _long_flat(regime(df["close"], self._fast, self._slow) > 0, df.index)


@dataclass
class StackedOutfitStrategy:
    """Long only when the whole MA ribbon is in bullish order (each shorter MA
    above the next-longer one). Stricter; trades less; a different hypothesis."""

    periods: list[int]
    name: str = "outfit_stacked"

    def __post_init__(self) -> None:
        self._periods = sorted(self.periods)

    def warmup_bars(self) -> int:
        return self._periods[-1]

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        mas = [sma(df["close"], p) for p in self._periods]
        bullish = pd.Series(True, index=df.index)
        for shorter, longer in zip(mas, mas[1:], strict=False):
            bullish &= shorter > longer
        return _long_flat(bullish, df.index)


@dataclass
class SystemsStrategy:
    """The source's '3 systems' trend rule: long when the fast/slow regime is
    positive AND price is above the key-level MA (a trend + confirmation gate)."""

    system: System
    require_key_level: bool = True
    name: str = "system"

    def warmup_bars(self) -> int:
        return max(self.system.trend_slow, self.system.key_level)

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        bullish = regime(close, self.system.trend_fast, self.system.trend_slow) > 0
        if self.require_key_level:
            bullish &= close > sma(close, self.system.key_level)
        return _long_flat(bullish, df.index)


def build_outfit_strategy(outfit: Outfit, **kwargs) -> OutfitStrategy:
    """Construct an OutfitStrategy from an :class:`Outfit` config entry."""
    strat = OutfitStrategy(periods=list(outfit.periods), **kwargs)
    strat.name = f"outfit:{outfit.id}"
    return strat
