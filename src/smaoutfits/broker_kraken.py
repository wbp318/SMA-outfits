"""Live Kraken broker (spot) via python-kraken-sdk.

Safety model:
- ``submit`` places a REAL order and only works when ``allow_live=True``. Anything
  else raises — so you cannot accidentally trade.
- ``validate_order`` uses Kraken's ``validate=true`` dry-run: the order is checked
  for errors and **no order is placed and no money moves**. This is how we exercise
  the trade path before going live.
- The factory (`broker.make_broker`) only constructs this with ``allow_live=True``
  when ``mode == live`` AND ``live.confirm`` is set — the same interlock as everywhere.

Read methods (balances, OHLCV, pair metadata) are always safe.
"""

from __future__ import annotations

import pandas as pd

from .config import AppConfig, resolve_credentials
from .data import OHLCV_COLUMNS
from .types import Fill, Order

__all__ = ["KrakenBroker"]

# ccxt-style timeframe -> Kraken OHLC interval (minutes). Kraken supports only these.
_INTERVAL = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240,
             "1d": 1440, "1w": 10080}

# Bases whose Kraken code differs from the common ticker.
_BASE_ALIAS = {"BTC": "XBT"}


class KrakenBroker:
    supports_live = True

    def __init__(self, key: str | None, secret: str | None, *, allow_live: bool = False,
                 taker_fee: float = 0.0026):
        from kraken.spot import Market, Trade, User
        self.allow_live = allow_live
        self.taker_fee = taker_fee
        self._market = Market()                       # public; no auth needed
        self._user = User(key=key, secret=secret)
        self._trade = Trade(key=key, secret=secret)
        self._pairs: dict | None = None               # asset-pairs cache

    @classmethod
    def from_config(cls, app: AppConfig, *, allow_live: bool = False) -> KrakenBroker:
        key, secret = resolve_credentials(app.exchange)
        return cls(key, secret, allow_live=allow_live, taker_fee=app.backtest.fee_pct)

    # -- symbol mapping ---------------------------------------------------
    def _asset_pairs(self) -> dict:
        if self._pairs is None:
            self._pairs = self._market.get_asset_pairs()
        return self._pairs

    def map_symbol(self, symbol: str) -> str:
        """'BTC/USD' -> Kraken altname ('XBTUSD'). Validated against asset pairs."""
        base, quote = symbol.split("/")
        base = _BASE_ALIAS.get(base, base)
        altname = f"{base}{quote}"
        for meta in self._asset_pairs().values():
            if meta.get("altname") == altname or meta.get("wsname") == f"{base}/{quote}":
                return meta["altname"]
        raise ValueError(f"no Kraken spot pair for {symbol!r} (tried {altname})")

    def pair_meta(self, symbol: str) -> dict:
        altname = self.map_symbol(symbol)
        for meta in self._asset_pairs().values():
            if meta.get("altname") == altname:
                return meta
        raise ValueError(f"no metadata for {symbol!r}")

    def _fmt_volume(self, symbol: str, qty: float) -> str:
        decimals = int(self.pair_meta(symbol).get("lot_decimals", 8))
        return f"{qty:.{decimals}f}"

    # -- read-only --------------------------------------------------------
    def get_balances(self) -> dict[str, float]:
        return {k: float(v) for k, v in self._user.get_account_balance().items()}

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h") -> pd.DataFrame:
        if timeframe not in _INTERVAL:
            raise ValueError(
                f"Kraken OHLC supports {list(_INTERVAL)} (not {timeframe!r}); resample finer bars")
        resp = self._market.get_ohlc(pair=self.map_symbol(symbol), interval=_INTERVAL[timeframe])
        resp.pop("last", None)
        (_, candles), = resp.items()
        df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close",
                                            "vwap", "volume", "count"])
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        return df.set_index("ts").sort_index()[OHLCV_COLUMNS].astype("float64")

    # -- orders -----------------------------------------------------------
    def validate_order(self, order: Order) -> dict:
        """Dry-run an order against Kraken (validate=true). Places NOTHING."""
        return self._trade.create_order(
            ordertype="market", side=order.side.value, pair=self.map_symbol(order.symbol),
            volume=self._fmt_volume(order.symbol, order.qty), validate=True)

    def submit(self, order: Order, mark_price: float, ts: float | None = None) -> Fill:
        """Place a REAL market order. Refuses unless allow_live is set."""
        if not self.allow_live:
            raise RuntimeError(
                "KrakenBroker.submit blocked: allow_live is False. Use validate_order, "
                "or run mode=live with live.confirm=true to enable real orders.")
        resp = self._trade.create_order(
            ordertype="market", side=order.side.value, pair=self.map_symbol(order.symbol),
            volume=self._fmt_volume(order.symbol, order.qty), validate=False)
        txid = resp.get("txid") if isinstance(resp, dict) else None
        # Estimate the fill at the mark; exact fills can be reconciled later via QueryOrders.
        fee = mark_price * order.qty * self.taker_fee
        return Fill(order.symbol, order.side, order.qty, mark_price, fee=fee, ts=ts,
                    meta={"txid": txid, "estimated_price": True})
