"""Offline tests for the data layer (no network)."""

from __future__ import annotations

import pandas as pd
import pytest

from smaoutfits.data import OHLCV_COLUMNS, MarketData, timeframe_to_ms


def test_timeframe_to_ms():
    assert timeframe_to_ms("1s") == 1_000
    assert timeframe_to_ms("15m") == 900_000
    assert timeframe_to_ms("1h") == 3_600_000
    assert timeframe_to_ms("1d") == 86_400_000
    with pytest.raises(ValueError):
        timeframe_to_ms("1y")


def _synthetic(n: int = 10) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {c: range(n) for c in OHLCV_COLUMNS}, index=idx, dtype="float64"
    ).rename_axis("ts")


def test_cache_round_trip(tmp_path):
    md = MarketData(exchange_name="kraken", cache_dir=tmp_path)
    df = _synthetic()
    md._write_cache("BTC/USD", "1h", df)
    back = md._read_cache("BTC/USD", "1h")
    assert back is not None
    assert str(back.index.tz) == "UTC"
    assert list(back.columns) == OHLCV_COLUMNS
    pd.testing.assert_frame_equal(back, df, check_freq=False)


def test_read_cache_missing_returns_none(tmp_path):
    md = MarketData(cache_dir=tmp_path)
    assert md._read_cache("ETH/USD", "1h") is None


def test_load_csv(tmp_path):
    path = tmp_path / "hist.csv"
    _synthetic().to_csv(path, index_label="ts")
    md = MarketData(cache_dir=tmp_path)
    df = md.load_csv(path)
    assert list(df.columns) == OHLCV_COLUMNS
    assert str(df.index.tz) == "UTC"
    assert len(df) == 10
