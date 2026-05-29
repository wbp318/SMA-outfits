"""Broker abstraction + a simulated broker for backtest and paper trading.

The Broker interface is the single seam between the engine and the outside world.
Backtest and paper trading both use ``SimulatedBroker`` (fills modeled against a
mark price with fees + slippage); they differ only in where prices come from
(historical replay vs the live public feed). The future ``KrakenBroker`` will
implement the same interface for real orders.

``make_broker`` is gate #1 of the no-live-orders interlock: it refuses to build a
real-order broker unless ``mode == live`` AND ``live.confirm`` is set.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .config import AppConfig
from .types import Fill, Order, Side

__all__ = ["Broker", "SimulatedBroker", "make_broker"]


@runtime_checkable
class Broker(Protocol):
    supports_live: bool

    def submit(self, order: Order, mark_price: float, ts: float | None = None) -> Fill:
        """Execute ``order`` and return the resulting Fill."""
        ...


class SimulatedBroker:
    """Models fills against a mark price. Buys fill slightly above and sells
    slightly below the mark (slippage), and pay a percentage fee — the same cost
    model the vectorized backtester uses, so paper results track backtests."""

    supports_live = False

    def __init__(self, fee_pct: float = 0.0026, slippage_pct: float = 0.0005):
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct

    def submit(self, order: Order, mark_price: float, ts: float | None = None) -> Fill:
        if order.side == Side.BUY:
            price = mark_price * (1.0 + self.slippage_pct)
        else:
            price = mark_price * (1.0 - self.slippage_pct)
        fee = price * order.qty * self.fee_pct
        return Fill(order.symbol, order.side, order.qty, price, fee=fee, ts=ts)


def make_broker(app: AppConfig) -> Broker:
    """Construct the broker for the configured mode.

    Gate #1: a real-order broker is only built when live trading is both selected
    and explicitly confirmed. Even then, the live broker isn't implemented yet —
    paper is built and validated first.
    """
    if app.mode in ("backtest", "paper"):
        return SimulatedBroker(app.backtest.fee_pct, app.backtest.slippage_pct)
    if app.mode == "live":
        if not app.live_orders_allowed():
            raise RuntimeError("live mode requires live.confirm=true (no-live-orders gate #1)")
        raise NotImplementedError(
            "KrakenBroker is not implemented yet — paper trading is built and validated first")
    raise ValueError(f"unknown mode {app.mode!r}")
