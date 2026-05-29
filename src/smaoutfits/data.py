"""Market-data access: fetch OHLCV from an exchange (via ccxt) and cache locally.

Design notes / honest caveats:
- We use ccxt because it is exchange-agnostic — the same code fetches Kraken now
  and other venues later. The live *order* layer may use a Kraken-native SDK; data
  fetching does not need to.
- KRAKEN HISTORY LIMIT: Kraken's public OHLC endpoint returns at most ~720 of the
  most recent candles per timeframe and does not paginate deep history. For long
  backtests you must either use a coarser timeframe, or import history from a file
  via ``load_csv``. ``fetch_ohlcv`` paginates where the exchange supports it and
  caps total bars; it will simply return whatever the exchange gives back.
- All timestamps are tz-aware UTC. The index is the candle OPEN time.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

__all__ = ["MarketData", "timeframe_to_ms", "OHLCV_COLUMNS"]

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# Multipliers for ccxt-style timeframe strings.
_UNIT_MS = {
    "s": 1_000,
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
}


def timeframe_to_ms(timeframe: str) -> int:
    """Convert a ccxt timeframe like '1h', '15m', '1d' to milliseconds."""
    tf = timeframe.strip().lower()
    unit = tf[-1]
    if unit not in _UNIT_MS:
        raise ValueError(f"unsupported timeframe unit in {timeframe!r}")
    qty = int(tf[:-1] or "1")
    return qty * _UNIT_MS[unit]


def _to_frame(rows: list[list[float]]) -> pd.DataFrame:
    """Turn ccxt's [[ts_ms, o, h, l, c, v], ...] into a tidy OHLCV frame."""
    df = pd.DataFrame(rows, columns=["ts", *OHLCV_COLUMNS])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df.astype("float64")


class MarketData:
    """Fetches and caches OHLCV. The ccxt client is created lazily so importing
    this module (and running offline unit tests) needs no network or ccxt."""

    def __init__(self, exchange_name: str = "kraken", cache_dir: str | Path = "data/cache"):
        self.exchange_name = exchange_name
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None

    # -- client -----------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            import ccxt  # imported lazily

            if not hasattr(ccxt, self.exchange_name):
                raise ValueError(f"ccxt has no exchange {self.exchange_name!r}")
            self._client = getattr(ccxt, self.exchange_name)({"enableRateLimit": True})
        return self._client

    # -- cache ------------------------------------------------------------
    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        safe = symbol.replace("/", "-")
        return self.cache_dir / f"{self.exchange_name}_{safe}_{timeframe}.csv"

    def _read_cache(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        path = self._cache_path(symbol, timeframe)
        if not path.exists():
            return None
        df = pd.read_csv(path, index_col="ts", parse_dates=["ts"])
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        return df

    def _write_cache(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        df.to_csv(self._cache_path(symbol, timeframe), index_label="ts")

    # -- fetch ------------------------------------------------------------
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        *,
        max_bars: int = 5000,
        use_cache: bool = True,
        page_limit: int = 720,
    ) -> pd.DataFrame:
        """Return an OHLCV DataFrame for ``symbol`` at ``timeframe``.

        Merges with any cached data and fetches only newer bars when possible.
        ``max_bars`` caps how far back pagination walks. Falls back gracefully
        to whatever the exchange returns (see the Kraken history caveat above).
        """
        cached = self._read_cache(symbol, timeframe) if use_cache else None
        tf_ms = timeframe_to_ms(timeframe)

        if cached is not None and not cached.empty:
            # Re-fetch from one bar before the last cached bar to fill the gap.
            last_ms = int(cached.index[-1].timestamp() * 1000)
            since = last_ms - tf_ms
        else:
            since = self.client.milliseconds() - max_bars * tf_ms

        rows: list[list[float]] = []
        cursor = since
        while True:
            batch = self.client.fetch_ohlcv(symbol, timeframe, since=cursor, limit=page_limit)
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < page_limit:
                break
            next_cursor = batch[-1][0] + tf_ms
            if next_cursor <= cursor:  # no forward progress (exchange capped us)
                break
            cursor = next_cursor
            if len(rows) >= max_bars:
                break
            time.sleep(self.client.rateLimit / 1000)

        fetched = _to_frame(rows) if rows else pd.DataFrame(columns=OHLCV_COLUMNS)
        if cached is not None:
            combined = pd.concat([cached, fetched])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        else:
            combined = fetched

        if use_cache and not combined.empty:
            self._write_cache(symbol, timeframe, combined)
        return combined.tail(max_bars)

    # -- import -----------------------------------------------------------
    def load_csv(self, path: str | Path) -> pd.DataFrame:
        """Import externally-sourced OHLCV (for deeper history than Kraken serves).

        Expects a 'ts'/'timestamp'/'date' column plus open/high/low/close/volume.
        """
        df = pd.read_csv(path)
        ts_col = next((c for c in ("ts", "timestamp", "date", "time") if c in df.columns), None)
        if ts_col is None:
            raise ValueError("CSV must have a ts/timestamp/date/time column")
        df = df.rename(columns={ts_col: "ts"})
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"CSV missing OHLCV columns: {missing}")
        return df.set_index("ts").sort_index()[OHLCV_COLUMNS].astype("float64")
