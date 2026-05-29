"""Tests for the portfolio ledger, simulated broker, factory gate, and engine."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smaoutfits.broker import SimulatedBroker, make_broker
from smaoutfits.config import AppConfig, ExchangeCfg, RiskConfig
from smaoutfits.engine import Engine
from smaoutfits.portfolio import Portfolio
from smaoutfits.risk import KillSwitch, RiskManager
from smaoutfits.strategy import OutfitStrategy
from smaoutfits.types import Fill, Order, OrderType, Side


def _app(mode="backtest", confirm=False):
    return AppConfig(mode=mode, exchange=ExchangeCfg(
        name="kraken", api_key_env="K", api_secret_env="S"), live={"confirm": confirm})


# -- portfolio --------------------------------------------------------------
def test_portfolio_buy_then_sell_tracks_cash_and_pnl():
    pf = Portfolio(cash=1000.0)
    pf.apply_fill(Fill("BTC/USD", Side.BUY, qty=1.0, price=100.0, fee=1.0))
    assert pf.cash == pytest.approx(899.0)            # 1000 - 100 - 1 fee
    assert pf.positions["BTC/USD"].qty == 1.0
    assert pf.positions["BTC/USD"].avg_price == pytest.approx(101.0)  # cost basis incl entry fee
    pf.apply_fill(Fill("BTC/USD", Side.SELL, qty=1.0, price=110.0, fee=1.0))
    assert "BTC/USD" not in pf.positions
    assert pf.cash == pytest.approx(899.0 + 110.0 - 1.0)
    # Net of BOTH fees: +10 price move - 1 entry - 1 exit = 8.
    assert pf.last_trade_pnl == pytest.approx(8.0)


def test_portfolio_equity_marks_to_market():
    pf = Portfolio(cash=500.0)
    pf.apply_fill(Fill("ETH/USD", Side.BUY, qty=2.0, price=100.0, fee=0.0))
    assert pf.cash == pytest.approx(300.0)
    assert pf.equity({"ETH/USD": 150.0}) == pytest.approx(300.0 + 2 * 150.0)


def test_sell_without_position_raises():
    pf = Portfolio(cash=0.0)
    with pytest.raises(ValueError):
        pf.apply_fill(Fill("BTC/USD", Side.SELL, qty=1.0, price=1.0))


# -- simulated broker -------------------------------------------------------
def test_simulated_broker_applies_slippage_and_fee():
    b = SimulatedBroker(fee_pct=0.001, slippage_pct=0.01)
    buy = b.submit(Order("BTC/USD", Side.BUY, 1.0, OrderType.MARKET), mark_price=100.0)
    assert buy.price == pytest.approx(101.0)          # buys fill above mark
    assert buy.fee == pytest.approx(101.0 * 0.001)
    sell = b.submit(Order("BTC/USD", Side.SELL, 1.0, OrderType.MARKET), mark_price=100.0)
    assert sell.price == pytest.approx(99.0)          # sells fill below mark


# -- factory gate -----------------------------------------------------------
def test_make_broker_backtest_and_paper_are_simulated():
    assert isinstance(make_broker(_app("backtest")), SimulatedBroker)
    assert isinstance(make_broker(_app("paper")), SimulatedBroker)


def test_make_broker_refuses_live_without_confirm():
    with pytest.raises(RuntimeError):
        make_broker(_app("live", confirm=False))


def test_make_broker_live_confirmed_returns_kraken_broker():
    # Gate passes -> a real KrakenBroker with live orders unlocked is returned.
    from smaoutfits.broker_kraken import KrakenBroker

    broker = make_broker(_app("live", confirm=True))
    assert isinstance(broker, KrakenBroker)
    assert broker.allow_live is True


# -- engine -----------------------------------------------------------------
def _ohlcv(closes, freq="1D"):
    idx = pd.date_range("2020-01-01", periods=len(closes), freq=freq, tz="UTC")
    c = pd.Series(closes, index=idx, dtype="float64")
    # Give some intrabar range so ATR is non-zero.
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0})


def _engine(tmp_path, mode="backtest"):
    app = _app(mode)
    risk = RiskConfig()
    kill = KillSwitch(risk.kill_switch, state_path=tmp_path / "ks.json")
    rm = RiskManager(app, risk, kill)
    pf = Portfolio(cash=10_000.0)
    return Engine(symbol="BTC/USD", strategy=OutfitStrategy([5, 20]), risk=rm,
                  portfolio=pf, broker=SimulatedBroker(0.0026, 0.0005)), pf


def test_engine_buys_in_uptrend_and_exits_in_downtrend(tmp_path):
    df = _ohlcv(list(np.linspace(100, 200, 60)) + list(np.linspace(200, 100, 60)))
    engine, pf = _engine(tmp_path)
    equity = engine.run_replay(df)
    sides = [f.side for f in pf.fills]
    assert Side.BUY in sides and Side.SELL in sides     # entered the uptrend, exited the downtrend
    assert len(equity) == len(df)
    assert equity.iloc[-1] > 0


def test_engine_acts_on_prior_bar_signal_not_same_bar(tmp_path):
    # No lookahead: the buy must fill on the bar AFTER the signal turned long,
    # at that bar's close — never on the bar whose close produced the signal.
    df = _ohlcv(list(np.linspace(100, 200, 60)))
    engine, pf = _engine(tmp_path)
    target = engine.strategy.target_position(df)
    warmup = engine.strategy.warmup_bars()
    engine.run_replay(df)
    buys = [f for f in pf.fills if f.side == Side.BUY]
    assert buys
    first_i = next(i for i in range(len(df)) if i > warmup and target.iloc[i - 1] == 1.0)
    assert buys[0].price == pytest.approx(float(df["close"].iloc[first_i]) * (1 + 0.0005))


def test_engine_respects_kill_switch_halt(tmp_path):
    df = _ohlcv(list(np.linspace(100, 200, 80)))
    engine, pf = _engine(tmp_path)
    engine.risk.kill.halt("manual test halt")
    engine.run_replay(df)
    # Halted before any entry -> no buys were ever placed.
    assert all(f.side != Side.BUY for f in pf.fills)
