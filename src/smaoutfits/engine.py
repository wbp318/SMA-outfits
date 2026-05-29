"""The trading engine — one event loop shared by backtest, paper, and live.

Per bar it runs the full pipeline: mark equity (and let the kill switch react) →
read the strategy's target → if it differs from what we hold, ask the
RiskManager to size/vet an order → execute through the broker → book the fill.

Backtest replay and live/paper differ only in where bars come from and which
broker is wired in — the decision logic is identical, so paper genuinely
exercises the same code path that live will.
"""

from __future__ import annotations

import pandas as pd

from .broker import Broker
from .indicators import atr
from .portfolio import Portfolio
from .risk import RiskManager
from .types import Side

__all__ = ["Engine"]


class Engine:
    def __init__(self, *, symbol: str, strategy, risk: RiskManager, portfolio: Portfolio,
                 broker: Broker, is_live: bool = False):
        self.symbol = symbol
        self.strategy = strategy
        self.risk = risk
        self.portfolio = portfolio
        self.broker = broker
        self.is_live = is_live
        self.decisions: list = []

    def _act(self, ts, price: float, target: float, atr_val: float | None):
        """Run one decision at a closed bar. Returns the Decision, or None if no
        action was warranted (already aligned with the target)."""
        marks = {self.symbol: price}
        equity = self.portfolio.equity(marks)
        # Mark equity first so the kill switch can halt before we open new risk.
        self.risk.kill.update_equity(equity, today=pd.Timestamp(ts).date().isoformat())

        held = self.portfolio.positions.get(self.symbol)
        have = held is not None and held.qty > 0

        if target > 0 and not have:
            side = Side.BUY
        elif target <= 0 and have:
            side = Side.SELL
        else:
            return None

        decision = self.risk.check_order(
            symbol=self.symbol, side=side, entry_price=price, equity=equity,
            open_positions=self.portfolio.positions,
            gross_exposure_value=self.portfolio.gross_exposure_value(marks),
            atr=atr_val, is_live_order=self.is_live,
            now=pd.Timestamp(ts).timestamp(),
        )
        self.decisions.append((ts, decision))
        if decision.allowed and decision.order is not None:
            fill = self.broker.submit(decision.order, price, ts=pd.Timestamp(ts).timestamp())
            self.portfolio.apply_fill(fill)
            if side == Side.SELL and self.portfolio.last_trade_pnl is not None:
                self.risk.kill.record_trade_result(self.portfolio.last_trade_pnl)
        return decision

    def on_bar(self, df: pd.DataFrame):
        """Process the most recent closed bar of ``df`` (live/paper entry point).

        No-lookahead: the decision uses the signal from the PRIOR closed bar and
        fills at the latest closed bar's price — the same one-bar lag the
        vectorized backtester applies (``target.shift(1)``). You cannot transact
        at the close you only learn the signal from.
        """
        if len(df) < 2:
            return None
        target = float(self.strategy.target_position(df).iloc[-2])
        atr_val = self._atr_at(df, -2)
        return self._act(df.index[-1], float(df["close"].iloc[-1]), target, atr_val)

    def run_replay(self, df: pd.DataFrame) -> pd.Series:
        """Replay a full OHLCV frame bar by bar; returns the equity curve.

        Signals/ATR are precomputed once (deterministic vectorized transforms).
        Each bar acts on the PREVIOUS bar's signal, filled at the current bar's
        close — matching the backtester's one-bar lag so paper and backtest are
        directly comparable and neither captures the move that triggered it.
        """
        target = self.strategy.target_position(df)
        atr_series = atr(df["high"], df["low"], df["close"], self.risk.risk.stops.atr_period)
        warmup = self.strategy.warmup_bars()
        equity_curve = []
        for i in range(len(df)):
            ts = df.index[i]
            price = float(df["close"].iloc[i])
            if i > warmup:  # need a prior, fully-formed bar's signal
                sig = float(target.iloc[i - 1])
                av = atr_series.iloc[i - 1]
                self._act(ts, price, sig, None if pd.isna(av) else float(av))
            equity_curve.append(self.portfolio.equity({self.symbol: price}))
        return pd.Series(equity_curve, index=df.index, name="equity")

    def _atr_at(self, df: pd.DataFrame, i: int) -> float | None:
        av = atr(df["high"], df["low"], df["close"], self.risk.risk.stops.atr_period).iloc[i]
        return None if pd.isna(av) else float(av)
