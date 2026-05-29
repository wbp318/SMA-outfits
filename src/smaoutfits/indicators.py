"""Pure moving-average indicators and signal primitives.

Everything here is a pure function of a price series — no I/O, no state, no
broker, no config. This is the mathematical core the strategy engine and
backtester build on, so it is the first thing worth getting right and testing.

Lookahead policy: an SMA value at bar ``t`` uses only closes up to and including
``t``. A crossover detected "at bar ``t``" is therefore knowable only at the
*close* of bar ``t``; the execution layer decides whether to fill on that close
or the next bar's open. Keeping that decision out of here avoids hidden
lookahead bias.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "sma",
    "multi_sma",
    "regime",
    "crossover",
    "key_level_breach",
    "atr",
]


def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple moving average over ``period`` bars.

    Returns NaN for the first ``period - 1`` bars (insufficient history) rather
    than a partial average, so downstream code never trades on a half-formed MA.
    """
    if period < 1:
        raise ValueError(f"SMA period must be >= 1, got {period}")
    return close.rolling(window=period, min_periods=period).mean()


def multi_sma(close: pd.Series, periods: list[int]) -> pd.DataFrame:
    """Compute several SMAs at once. Columns are named ``sma_<period>``."""
    if not periods:
        raise ValueError("periods must be non-empty")
    out = {f"sma_{p}": sma(close, p) for p in periods}
    return pd.DataFrame(out, index=close.index)


def regime(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """Trend regime: +1 when fast SMA is above slow SMA, -1 when below, else 0.

    This is the source repo's "positive / negative" system state expressed
    numerically. NaN (warm-up) bars are 0 (no position).
    """
    f = sma(close, fast)
    s = sma(close, slow)
    state = pd.Series(0, index=close.index, dtype="int64")
    state[f > s] = 1
    state[f < s] = -1
    state[f.isna() | s.isna()] = 0
    return state


def crossover(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """Discrete crossover events from the fast/slow regime.

    +1 on the bar where fast crosses *above* slow (golden cross / buy event),
    -1 where fast crosses *below* slow (death cross / sell event), 0 otherwise.
    Only the transition bar is non-zero, so each event fires exactly once.
    """
    r = regime(close, fast, slow)
    events = r.diff().fillna(0)
    # Only count genuine sign flips between two *formed* regimes. Transitions that
    # touch the neutral/warm-up state (regime == 0) are not real crossovers and
    # must not fire a trade signal.
    events[(r == 0) | (r.shift(1).fillna(0) == 0)] = 0
    return np.sign(events).astype("int64")


def key_level_breach(close: pd.Series, period: int) -> pd.Series:
    """+1 when close is above its ``period`` SMA, -1 below, 0 during warm-up.

    Used for the source's "key level" rules (e.g. SPX vs its MA200) and the
    high-volatility "candle close above/below the middle MA" variant.
    """
    level = sma(close, period)
    out = pd.Series(0, index=close.index, dtype="int64")
    out[close > level] = 1
    out[close < level] = -1
    out[level.isna()] = 0
    return out


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range (Wilder's smoothing) — used for volatility-based
    position sizing and stop distances.

    True range is max(high-low, |high-prev_close|, |low-prev_close|); ATR is the
    Wilder RMA of that (an EWM with alpha = 1/period). NaN until ``period`` bars.
    """
    if period < 1:
        raise ValueError(f"ATR period must be >= 1, got {period}")
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    # Wilder's RMA; min_periods so early bars are NaN rather than partial.
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
