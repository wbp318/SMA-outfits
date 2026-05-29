"""Mocked tests for KrakenBroker — no network. The safety-critical one is that
submit refuses to place a real order unless allow_live is explicitly set."""

from __future__ import annotations

import pytest

from smaoutfits.broker_kraken import KrakenBroker
from smaoutfits.types import Order, OrderType, Side

PAIRS = {
    "XXBTZUSD": {"altname": "XBTUSD", "wsname": "XBT/USD", "lot_decimals": 4, "ordermin": "0.0001"},
    "XETHZUSD": {"altname": "ETHUSD", "wsname": "ETH/USD", "lot_decimals": 8, "ordermin": "0.01"},
}


class _FakeTrade:
    def __init__(self):
        self.calls: list[dict] = []

    def create_order(self, **kw):
        self.calls.append(kw)
        return {"descr": {"order": "buy 0.0001 XBTUSD @ market"}, "txid": ["OTEST-1"]}


def _broker(allow_live: bool = False) -> KrakenBroker:
    kb = KrakenBroker(key="k", secret="s", allow_live=allow_live)
    kb._pairs = PAIRS          # inject cached asset pairs (no network)
    kb._trade = _FakeTrade()   # capture order calls
    return kb


def test_map_symbol_handles_btc_alias():
    kb = _broker()
    assert kb.map_symbol("BTC/USD") == "XBTUSD"   # BTC -> XBT
    assert kb.map_symbol("ETH/USD") == "ETHUSD"


def test_map_symbol_unknown_raises():
    with pytest.raises(ValueError):
        _broker().map_symbol("DOGE/USD")


def test_fmt_volume_respects_lot_decimals():
    assert _broker()._fmt_volume("BTC/USD", 0.123456789) == "0.1235"   # 4 decimals


def test_validate_order_sends_validate_true_and_no_real_order():
    kb = _broker()
    kb.validate_order(Order("BTC/USD", Side.BUY, 0.0001, OrderType.MARKET))
    call = kb._trade.calls[-1]
    assert call["validate"] is True
    assert call["pair"] == "XBTUSD"
    assert call["side"] == "buy"


def test_submit_blocked_without_allow_live():
    kb = _broker(allow_live=False)
    with pytest.raises(RuntimeError):
        kb.submit(Order("BTC/USD", Side.BUY, 0.0001, OrderType.MARKET), mark_price=50_000)
    assert kb._trade.calls == []   # nothing was ever sent to Kraken


def test_submit_places_real_order_only_when_allowed():
    kb = _broker(allow_live=True)
    fill = kb.submit(Order("BTC/USD", Side.BUY, 0.0001, OrderType.MARKET), mark_price=50_000)
    call = kb._trade.calls[-1]
    assert call["validate"] is False
    assert fill.qty == pytest.approx(0.0001)
    assert fill.meta["txid"] == ["OTEST-1"]
