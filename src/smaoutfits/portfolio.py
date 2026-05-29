"""Portfolio ledger — cash, positions, equity, and realized PnL.

Single source of truth for "what do we hold and what is it worth". Used
identically by the backtest, paper, and (later) live engines so behavior is
consistent across all three.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Fill, Position, Side

__all__ = ["Portfolio"]


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    last_trade_pnl: float | None = None     # PnL of the most recent close (for the kill switch)
    fills: list[Fill] = field(default_factory=list)

    def apply_fill(self, fill: Fill) -> None:
        """Update cash and positions from an executed fill."""
        self.fills.append(fill)
        if fill.side == Side.BUY:
            # Cost basis INCLUDES the entry fee, so realized PnL on the eventual
            # sell is net of both fees — otherwise a fee-losing trade can be
            # scored as a win and slip past the consecutive-loss kill switch.
            buy_cost = fill.qty * fill.price + fill.fee
            self.cash -= buy_cost
            held = self.positions.get(fill.symbol)
            if held is None:
                self.positions[fill.symbol] = Position(fill.symbol, fill.qty, buy_cost / fill.qty)
            else:
                new_qty = held.qty + fill.qty
                held.avg_price = (held.qty * held.avg_price + buy_cost) / new_qty
                held.qty = new_qty
        else:  # SELL
            held = self.positions.get(fill.symbol)
            if held is None or held.qty <= 0:
                raise ValueError(f"sell fill for {fill.symbol} with no position")
            qty = min(fill.qty, held.qty)
            self.cash += qty * fill.price - fill.fee
            # Realized PnL vs average cost (entry fee was already paid at buy time).
            self.last_trade_pnl = qty * (fill.price - held.avg_price) - fill.fee
            self.realized_pnl += self.last_trade_pnl
            held.qty -= qty
            if held.qty <= 1e-12:
                del self.positions[fill.symbol]

    def equity(self, marks: dict[str, float]) -> float:
        """Cash plus mark-to-market value of open positions."""
        mtm = sum(p.qty * marks.get(sym, p.avg_price) for sym, p in self.positions.items())
        return self.cash + mtm

    def gross_exposure_value(self, marks: dict[str, float]) -> float:
        """Total mark-to-market value deployed in positions."""
        return sum(p.qty * marks.get(sym, p.avg_price) for sym, p in self.positions.items())
