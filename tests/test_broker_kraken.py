"""Mocked tests for KrakenBroker — no network. The safety-critical ones: submit
refuses without allow_live, books the ACTUAL fill (not the mark), fails closed when
unconfirmed, and rejects orders that would slip too far."""

from __future__ import annotations

import pytest

from smaoutfits.broker_kraken import KrakenBroker
from smaoutfits.types import Order, OrderType, Side

PAIRS = {
    "XXBTZUSD": {"altname": "XBTUSD", "wsname": "XBT/USD", "lot_decimals": 4, "ordermin": "0.0001"},
    "XETHZUSD": {"altname": "ETHUSD", "wsname": "ETH/USD", "lot_decimals": 8, "ordermin": "0.01"},
}


class _FakeMarket:
    def __init__(self, ask=50_010.0, bid=49_990.0):
        self.ask, self.bid = ask, bid

    def get_asset_pairs(self):
        return PAIRS

    def get_ticker(self, pair):
        return {pair: {"a": [str(self.ask), "1", "1"], "b": [str(self.bid), "1", "1"]}}


_FILLED = {"status": "closed", "vol_exec": "0.0001", "cost": "5.001",
           "price": "50010.0", "fee": "0.013"}
_SENTINEL = object()


class _FakeUser:
    def __init__(self, order=_SENTINEL):
        # default = a fully-filled order; explicit None = unconfirmed (returns {})
        self.order = _FILLED if order is _SENTINEL else order

    def get_orders_info(self, txid):
        return {} if self.order is None else {txid: self.order}


class _FakeTrade:
    def __init__(self):
        self.calls: list[dict] = []

    def create_order(self, **kw):
        self.calls.append(kw)
        return {"descr": {"order": "buy market"}, "txid": ["OTEST-1"]}


def _broker(allow_live=False, ask=50_010.0, bid=49_990.0, order="default") -> KrakenBroker:
    kb = KrakenBroker(key="k", secret="s", allow_live=allow_live)
    kb._pairs = PAIRS
    kb._market = _FakeMarket(ask, bid)
    kb._user = _FakeUser() if order == "default" else _FakeUser(order)
    kb._trade = _FakeTrade()
    return kb


def test_map_symbol_handles_btc_alias():
    kb = _broker()
    assert kb.map_symbol("BTC/USD") == "XBTUSD"
    assert kb.map_symbol("ETH/USD") == "ETHUSD"


def test_map_symbol_unknown_raises():
    with pytest.raises(ValueError):
        _broker().map_symbol("DOGE/USD")


def test_prepare_volume_rounds_down_to_lot_decimals():
    # 0.123456789 -> floor to 4 decimals -> 0.1234 (never rounds UP past approval)
    assert _broker()._prepare_volume("BTC/USD", 0.123456789) == "0.1234"


def test_prepare_volume_rejects_below_ordermin():
    with pytest.raises(ValueError):
        _broker()._prepare_volume("ETH/USD", 0.005)   # ETH ordermin is 0.01


def test_validate_order_sends_validate_true_and_no_real_order():
    kb = _broker()
    kb.validate_order(Order("BTC/USD", Side.BUY, 0.0001, OrderType.MARKET))
    call = kb._trade.calls[-1]
    assert call["validate"] is True and call["pair"] == "XBTUSD" and call["side"] == "buy"


def test_submit_blocked_without_allow_live():
    kb = _broker(allow_live=False)
    with pytest.raises(RuntimeError):
        kb.submit(Order("BTC/USD", Side.BUY, 0.0001, OrderType.MARKET), mark_price=50_000)
    assert kb._trade.calls == []   # nothing was ever sent to Kraken


def test_submit_books_actual_fill_not_mark():
    kb = _broker(allow_live=True)
    fill = kb.submit(Order("BTC/USD", Side.BUY, 0.0001, OrderType.MARKET), mark_price=50_000)
    assert kb._trade.calls[-1]["validate"] is False
    # Fill uses Kraken's reported price (50010) and fee (0.013), NOT the 50000 mark.
    assert fill.price == pytest.approx(50_010.0)
    assert fill.fee == pytest.approx(0.013)
    assert fill.qty == pytest.approx(0.0001)
    assert fill.meta["txid"] == "OTEST-1"


def test_submit_fails_closed_when_fill_unconfirmed():
    kb = _broker(allow_live=True, order=None)   # get_orders_info returns {}
    with pytest.raises(RuntimeError):
        kb.submit(Order("BTC/USD", Side.BUY, 0.0001, OrderType.MARKET), mark_price=50_000)


def test_submit_rejects_excessive_slippage():
    kb = _broker(allow_live=True, ask=60_000.0)   # ask 20% above the mark
    with pytest.raises(RuntimeError):
        kb.submit(Order("BTC/USD", Side.BUY, 0.0001, OrderType.MARKET), mark_price=50_000)
    assert kb._trade.calls == []   # rejected before any order was sent
