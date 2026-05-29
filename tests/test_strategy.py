"""Tests for the strategy layer."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smaoutfits.config import System
from smaoutfits.strategy import (
    CrossoverStrategy,
    OutfitStrategy,
    StackedOutfitStrategy,
    SystemsStrategy,
)


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="1h", tz="UTC")
    c = pd.Series(closes, index=idx, dtype="float64")
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0})


def test_target_position_is_long_flat_only():
    df = _frame(list(range(1, 60)) + list(range(60, 1, -1)))
    pos = OutfitStrategy(periods=[5, 20]).target_position(df)
    assert set(pos.unique()).issubset({0.0, 1.0})
    assert pos.index.equals(df.index)


def test_outfit_long_in_uptrend_flat_in_downtrend():
    # Strong up then strong down; fast(5) vs slow(20).
    df = _frame(list(np.linspace(100, 200, 60)) + list(np.linspace(200, 100, 60)))
    pos = OutfitStrategy(periods=[5, 20]).target_position(df)
    # Long somewhere in the clear uptrend, flat somewhere in the clear downtrend.
    assert pos.iloc[40] == 1.0
    assert pos.iloc[-10] == 0.0


def test_outfit_uses_sorted_shortest_vs_longest():
    s = OutfitStrategy(periods=[200, 10, 50])
    assert s._fast == 10 and s._slow == 200


def test_crossover_rejects_fast_ge_slow():
    with pytest.raises(ValueError):
        CrossoverStrategy(fast=50, slow=50)


def test_stacked_is_stricter_than_crossover():
    df = _frame(list(np.linspace(100, 200, 80)))
    cross = OutfitStrategy(periods=[5, 10, 20]).target_position(df)
    stacked = StackedOutfitStrategy(periods=[5, 10, 20]).target_position(df)
    # Stacked requires full ribbon alignment, so it is long no more often.
    assert stacked.sum() <= cross.sum()


def test_systems_strategy_requires_key_level():
    sysdef = System(id="t", instrument="X", outfit_id="o", timeframes=["1h"],
                    trend_fast=5, trend_slow=20, key_level=30, high_vol_level=20)
    df = _frame(list(np.linspace(100, 200, 80)))
    pos = SystemsStrategy(sysdef).target_position(df)
    assert set(pos.unique()).issubset({0.0, 1.0})
    # The 30-period key-level MA only forms at index 29, so bars 0..28 are flat.
    assert pos.iloc[:29].eq(0.0).all()
