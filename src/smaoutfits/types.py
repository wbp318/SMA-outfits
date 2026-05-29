"""Core dataclasses and enums shared across strategy, risk, broker, and engine.

Intentionally tiny and dependency-free — these are the nouns the whole system
passes around so backtest, paper, and live all speak the same shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = ["Side", "OrderType", "Order", "Decision", "Position", "Fill"]


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class Order:
    symbol: str
    side: Side
    qty: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    client_id: str | None = None


@dataclass
class Decision:
    """The risk layer's verdict on a proposed order."""

    allowed: bool
    rule: str                      # which rule fired (for the audit trail)
    reason: str
    order: Order | None = None     # the sized, stop-attached order when allowed

    @classmethod
    def reject(cls, rule: str, reason: str) -> Decision:
        return cls(allowed=False, rule=rule, reason=reason, order=None)

    @classmethod
    def allow(cls, rule: str, order: Order, reason: str = "ok") -> Decision:
        return cls(allowed=True, rule=rule, reason=reason, order=order)


@dataclass
class Position:
    symbol: str
    qty: float
    avg_price: float

    def value(self, mark_price: float) -> float:
        return self.qty * mark_price


@dataclass
class Fill:
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float = 0.0
    ts: float | None = None
    meta: dict = field(default_factory=dict)
