"""Tests for the vectorized backtester — focus on correctness and no-lookahead."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smaoutfits.backtest import buy_and_hold, evaluate_target, run_backtest
from smaoutfits.strategy import OutfitStrategy


def _frame(closes: list[float], tf_freq: str = "1D") -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(closes), freq=tf_freq, tz="UTC")
    c = pd.Series(closes, index=idx, dtype="float64")
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0})


def test_buy_and_hold_matches_price_change_minus_one_fee():
    df = _frame([100, 110, 121])  # +10% then +10%
    res = buy_and_hold(df, "1d", fee_pct=0.001, slippage_pct=0.0)
    # Entry fee is paid before the gains compound: (1 + .1 - .001) * (1 + .1) - 1.
    assert res.total_return == pytest.approx((1.099 * 1.1) - 1.0, rel=1e-9)
    assert res.n_trades == 1


def test_no_lookahead_signal_acts_next_bar():
    # Price jumps once at bar 2. A target that turns long exactly on the jump
    # bar must NOT capture that bar's return (it is shifted to the next bar).
    df = _frame([100, 100, 200, 200])
    target = pd.Series([0, 0, 1, 1], index=df.index, dtype="float64")
    res = evaluate_target("x", "1d", target, df["close"], fee_pct=0.0, slippage_pct=0.0)
    # Position is applied from bar 3 onward; the 100->200 jump (bar 2) is missed.
    # Bars 3->4 are flat in price, so strategy return is ~0 (minus no fees).
    assert res.total_return == pytest.approx(0.0, abs=1e-9)


def test_costs_reduce_return_on_each_turnover():
    df = _frame([100, 100, 100, 100])
    flip = pd.Series([1, 0, 1, 0], index=df.index, dtype="float64")
    res = evaluate_target("x", "1d", flip, df["close"], fee_pct=0.01, slippage_pct=0.0)
    # Flat prices but repeated turnover -> strictly negative return from fees.
    assert res.total_return < 0
    assert res.n_trades >= 2


def test_formed_flag_false_when_history_too_short():
    df = _frame(list(np.linspace(100, 110, 20)))
    res = run_backtest(OutfitStrategy(periods=[10, 200]), df, "1d")
    assert res.formed is False  # 200-period MA can't form in 20 bars


def test_max_drawdown_is_non_positive():
    df = _frame([100, 120, 60, 90])
    res = buy_and_hold(df, "1d", fee_pct=0.0, slippage_pct=0.0)
    assert res.max_drawdown <= 0.0
