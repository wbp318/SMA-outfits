"""Persistent paper-trading session state.

Forward paper trading runs over real time: each newly *closed* bar, we step the
engine with fake money and append to an equity curve. That only works if state
survives between ticks/restarts — so this persists cash, positions, realized PnL,
the last processed bar, and the equity history to disk with an atomic write.

This is deliberately separate from the kill-switch state (which has its own file)
so the two concerns can't corrupt each other.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .portfolio import Portfolio
from .types import Position

__all__ = ["PaperSession"]


@dataclass
class PaperSession:
    symbol: str
    initial_cash: float
    cash: float
    positions: dict[str, list[float]] = field(default_factory=dict)   # sym -> [qty, avg_price]
    realized_pnl: float = 0.0
    last_bar_ts: str | None = None                                    # ISO of last processed bar
    equity_curve: list[list] = field(default_factory=list)            # [[iso, equity], ...]
    n_fills: int = 0

    # -- persistence ------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path, *, symbol: str, initial_cash: float) -> PaperSession:
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(**data)
        return cls(symbol=symbol, initial_cash=initial_cash, cash=initial_cash)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)

    # -- portfolio bridge -------------------------------------------------
    def to_portfolio(self) -> Portfolio:
        pf = Portfolio(cash=self.cash, realized_pnl=self.realized_pnl)
        for sym, (qty, avg) in self.positions.items():
            pf.positions[sym] = Position(sym, qty, avg)
        return pf

    def absorb(self, pf: Portfolio, bar_ts: str, equity: float) -> None:
        """Pull the post-tick portfolio state back into the persisted session."""
        self.cash = pf.cash
        self.realized_pnl = pf.realized_pnl
        self.positions = {s: [p.qty, p.avg_price] for s, p in pf.positions.items()}
        self.n_fills += len(pf.fills)
        self.last_bar_ts = bar_ts
        self.equity_curve.append([bar_ts, equity])

    def already_processed(self, bar_ts: str) -> bool:
        return self.last_bar_ts == bar_ts
