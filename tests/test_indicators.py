"""Tests for the pure indicator core."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smaoutfits.indicators import (
    crossover,
    key_level_breach,
    multi_sma,
    regime,
    sma,
)


def test_sma_basic_values():
    s = pd.Series([1, 2, 3, 4, 5], dtype="float64")
    out = sma(s, 3)
    # First two are warm-up (NaN), then trailing means.
    assert out.iloc[:2].isna().all()
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[3] == pytest.approx(3.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_sma_rejects_bad_period():
    with pytest.raises(ValueError):
        sma(pd.Series([1.0, 2.0]), 0)


def test_multi_sma_columns():
    s = pd.Series(np.arange(10), dtype="float64")
    df = multi_sma(s, [2, 5])
    assert list(df.columns) == ["sma_2", "sma_5"]
    assert len(df) == 10


def test_regime_and_no_lookahead_during_warmup():
    # Rising then falling line so fast/slow relationship flips.
    prices = pd.Series(
        [10, 11, 12, 13, 14, 15, 14, 13, 12, 11, 10], dtype="float64"
    )
    r = regime(prices, fast=2, slow=4)
    # Warm-up region (until slow SMA exists) must be flat 0.
    assert (r.iloc[:3] == 0).all()
    # While clearly rising, fast should sit above slow -> +1 somewhere.
    assert (r == 1).any()
    # While clearly falling, fast below slow -> -1 somewhere.
    assert (r == -1).any()


def test_crossover_fires_only_on_genuine_flips():
    # Down (regime forms bearish), then up (genuine golden cross +1), then down
    # (genuine death cross -1). Both events are real flips between formed regimes.
    prices = pd.Series(
        [20, 19, 18, 17, 16, 15, 16, 18, 20, 22, 24, 22, 20, 18, 16, 14], dtype="float64"
    )
    x = crossover(prices, fast=2, slow=4)
    assert set(x.unique()).issubset({-1, 0, 1})
    assert (x == 1).sum() >= 1
    assert (x == -1).sum() >= 1


def test_crossover_masks_warmup_transition():
    # A purely rising series: regime goes 0 (warm-up) -> +1 once formed. That
    # 0 -> +1 is NOT a crossover and must not fire a buy event.
    prices = pd.Series(np.linspace(100, 200, 40), dtype="float64")
    x = crossover(prices, fast=5, slow=20)
    assert (x == 0).all()


def test_key_level_breach():
    prices = pd.Series([10, 10, 10, 10, 12, 8], dtype="float64")
    b = key_level_breach(prices, period=3)
    assert b.iloc[:2].eq(0).all()  # warm-up
    assert b.iloc[4] == 1   # 12 above the ~10 average
    assert b.iloc[5] == -1  # 8 below it
