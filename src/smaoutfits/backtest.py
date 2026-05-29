"""A small, correct, vectorized long/flat backtester.

This is deliberately simple and auditable — it is both the engine behind the
empirical outfit study and the independent reconciliation oracle that vectorbt's
results will later be checked against.

Model:
- A strategy emits a target position in {0, 1} for each bar (long or flat).
- Execution is lagged one bar (``shift(1)``): a signal computed from bar t's
  close is acted on at bar t+1. This is the single most important anti-lookahead
  guard — without it backtests look magical and live results disappoint.
- Each change in position pays ``fee_pct + slippage_pct`` on the traded fraction.
- Bar return = position * close.pct_change() - transaction cost.

Metrics are annualized using the bar size so 1h and 1d results are comparable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data import timeframe_to_ms

__all__ = ["BacktestResult", "run_backtest", "buy_and_hold", "evaluate_target"]

_MS_PER_YEAR = 365.25 * 24 * 60 * 60 * 1000


def _bars_per_year(timeframe: str) -> float:
    return _MS_PER_YEAR / timeframe_to_ms(timeframe)


@dataclass
class BacktestResult:
    name: str
    timeframe: str
    n_bars: int
    formed: bool                 # did the strategy have enough history to form?
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float          # negative number, e.g. -0.32
    exposure: float              # fraction of bars held long
    n_trades: int
    equity: pd.Series = field(repr=False, default_factory=pd.Series)

    def as_row(self) -> dict:
        return {
            "name": self.name,
            "timeframe": self.timeframe,
            "n_bars": self.n_bars,
            "formed": self.formed,
            "total_return": self.total_return,
            "cagr": self.cagr,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "exposure": self.exposure,
            "n_trades": self.n_trades,
        }


def _metrics(name: str, timeframe: str, target: pd.Series, close: pd.Series,
             fee_pct: float, slippage_pct: float, formed: bool) -> BacktestResult:
    target = target.reindex(close.index).fillna(0.0).clip(0.0, 1.0)
    position = target.shift(1).fillna(0.0)          # act next bar — no lookahead
    bar_ret = close.pct_change().fillna(0.0)
    turnover = position.diff().abs().fillna(position.abs())
    cost = turnover * (fee_pct + slippage_pct)
    strat_ret = position * bar_ret - cost

    equity = (1.0 + strat_ret).cumprod()
    n_bars = len(close)
    bpy = _bars_per_year(timeframe)

    total_return = float(equity.iloc[-1] - 1.0) if n_bars else 0.0
    years = n_bars / bpy if bpy else 0.0
    cagr = float(equity.iloc[-1] ** (1 / years) - 1.0) if years > 0 and equity.iloc[-1] > 0 else 0.0
    std = strat_ret.std()
    sharpe = float(strat_ret.mean() / std * np.sqrt(bpy)) if std > 0 else 0.0
    running_max = equity.cummax()
    max_dd = float((equity / running_max - 1.0).min()) if n_bars else 0.0
    exposure = float((position > 0).mean()) if n_bars else 0.0
    n_trades = int((turnover > 0).sum())

    return BacktestResult(
        name=name, timeframe=timeframe, n_bars=n_bars, formed=formed,
        total_return=total_return, cagr=cagr, sharpe=sharpe,
        max_drawdown=max_dd, exposure=exposure, n_trades=n_trades, equity=equity,
    )


def evaluate_target(name: str, timeframe: str, target: pd.Series, close: pd.Series, *,
                    fee_pct: float = 0.0026, slippage_pct: float = 0.0005,
                    formed: bool = True) -> BacktestResult:
    """Score a precomputed target-position series against close prices.

    Lets callers (e.g. the study's train/test split) slice an already-warmed
    position series without restarting MA warm-up on the slice.
    """
    return _metrics(name, timeframe, target, close, fee_pct, slippage_pct, formed)


def run_backtest(strategy, df: pd.DataFrame, timeframe: str, *,
                 fee_pct: float = 0.0026, slippage_pct: float = 0.0005) -> BacktestResult:
    """Backtest a strategy on an OHLCV frame.

    ``formed`` is False when the frame is shorter than the strategy's warm-up,
    meaning its longest MA never fully forms — the result is reported but should
    be read as "insufficient history", not as a real edge (or lack of one).
    """
    warmup = strategy.warmup_bars()
    formed = len(df) > warmup + 1
    target = strategy.target_position(df)
    return _metrics(strategy.name, timeframe, target, df["close"], fee_pct, slippage_pct, formed)


def buy_and_hold(df: pd.DataFrame, timeframe: str, *,
                 fee_pct: float = 0.0026, slippage_pct: float = 0.0005) -> BacktestResult:
    """Always-long benchmark (pays one entry cost). The bar every strategy must beat."""
    target = pd.Series(1.0, index=df.index)
    res = _metrics("buy_and_hold", timeframe, target, df["close"], fee_pct, slippage_pct, True)
    return res
