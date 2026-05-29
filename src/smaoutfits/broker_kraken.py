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

import math

import pandas as pd

from .config import AppConfig, resolve_credentials
from .data import OHLCV_COLUMNS
from .types import Fill, Order, Side

__all__ = ["KrakenBroker"]

# ccxt-style timeframe -> Kraken OHLC interval (minutes). Kraken supports only these.
_INTERVAL = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240,
             "1d": 1440, "1w": 10080}

# Bases whose Kraken code differs from the common ticker.
_BASE_ALIAS = {"BTC": "XBT"}


class KrakenBroker:
    supports_live = True

    def __init__(self, key: str | None, secret: str | None, *, allow_live: bool = False,
                 max_slippage_pct: float | None = 0.01):
        from kraken.spot import Market, Trade, User
        self.allow_live = allow_live
        self.max_slippage_pct = max_slippage_pct      # reject live fills deviating more than this
        self._market = Market()                       # public; no auth needed
        self._user = User(key=key, secret=secret)
        self._trade = Trade(key=key, secret=secret)
        self._pairs: dict | None = None               # asset-pairs cache

    @classmethod
    def from_config(cls, app: AppConfig, *, allow_live: bool = False,
                    max_slippage_pct: float | None = 0.01) -> KrakenBroker:
        key, secret = resolve_credentials(app.exchange)
        return cls(key, secret, allow_live=allow_live, max_slippage_pct=max_slippage_pct)

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

    def _prepare_volume(self, symbol: str, qty: float, mark_price: float | None = None) -> str:
        """Round volume DOWN to the pair's lot precision (so the executed notional
        never exceeds what risk approved) and enforce Kraken's minimums. Fail-closed."""
        meta = self.pair_meta(symbol)
        decimals = int(meta.get("lot_decimals", 8))
        factor = 10 ** decimals
        vol = math.floor(qty * factor) / factor
        ordermin = float(meta.get("ordermin", 0) or 0)
        if vol <= 0 or vol < ordermin:
            raise ValueError(f"volume {vol} below Kraken ordermin {ordermin} for {symbol}")
        costmin = meta.get("costmin")
        if costmin is not None and mark_price is not None and vol * mark_price < float(costmin):
            raise ValueError(f"notional {vol * mark_price} below costmin {costmin} for {symbol}")
        return f"{vol:.{decimals}f}"

    def _check_slippage(self, order: Order, mark_price: float) -> None:
        """Reject a live order if the touch price has run too far from the mark."""
        if self.max_slippage_pct is None:
            return
        ticker = self._market.get_ticker(pair=self.map_symbol(order.symbol))
        t = next(iter(ticker.values())) if isinstance(ticker, dict) and ticker else None
        if not t:
            raise RuntimeError(f"cannot read ticker for {order.symbol}; refusing to trade blind")
        exec_px = float(t["a"][0]) if order.side == Side.BUY else float(t["b"][0])
        deviation = abs(exec_px - mark_price) / mark_price
        if deviation > self.max_slippage_pct:
            raise RuntimeError(
                f"expected slippage {deviation:.2%} exceeds max {self.max_slippage_pct:.2%} "
                f"for {order.symbol}; rejecting order")

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
            volume=self._prepare_volume(order.symbol, order.qty), validate=True)

    def submit(self, order: Order, mark_price: float, ts: float | None = None) -> Fill:
        """Place a REAL market order and book the ACTUAL fill. Refuses unless
        allow_live is set; fails closed if the fill can't be confirmed."""
        if not self.allow_live:
            raise RuntimeError(
                "KrakenBroker.submit blocked: allow_live is False. Use validate_order, "
                "or run mode=live with live.confirm=true to enable real orders.")
        self._check_slippage(order, mark_price)
        volume = self._prepare_volume(order.symbol, order.qty, mark_price)
        resp = self._trade.create_order(
            ordertype="market", side=order.side.value, pair=self.map_symbol(order.symbol),
            volume=volume, validate=False)
        txids = resp.get("txid") if isinstance(resp, dict) else None
        if not txids:
            raise RuntimeError(f"Kraken returned no txid; order status unknown: {resp}")
        return self._fill_from_order(order, txids[0], ts)

    def _fill_from_order(self, order: Order, txid: str, ts: float | None) -> Fill:
        """Build the Fill from Kraken's record of the actual execution (price, fee,
        executed volume) — never from the mark. Fail-closed if unconfirmed."""
        info = self._user.get_orders_info(txid=txid)
        o = info.get(txid) if isinstance(info, dict) else None
        if not o:
            raise RuntimeError(f"could not query order {txid} to confirm the fill")
        vol_exec = float(o.get("vol_exec", 0) or 0)
        if vol_exec <= 0:
            raise RuntimeError(f"order {txid} reports no executed volume; fail-closed")
        cost = float(o.get("cost", 0) or 0)
        avg_price = float(o.get("price") or (cost / vol_exec))
        fee = float(o.get("fee", 0) or 0)
        return Fill(order.symbol, order.side, vol_exec, avg_price, fee=fee, ts=ts,
                    meta={"txid": txid})
