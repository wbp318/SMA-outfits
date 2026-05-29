"""The risk layer — the safety system that sits between strategy signals and the
broker. Nothing reaches a broker without passing through here.

Two ideas drive the design:
1. **Fail-closed.** When in doubt, refuse. A corrupt kill-switch state, a missing
   stop, an unknown side — all reject rather than risk capital.
2. **The kill switch survives crashes.** Its state is persisted with an atomic
   write so a process that dies mid-trade and restarts cannot bypass a halt.

The RiskManager only *sizes and vets* orders; it never talks to an exchange. The
"can this become a real order" interlock is enforced here too (defense in depth)
in addition to the broker factory and order router.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from .config import AppConfig, RiskConfig
from .types import Decision, Order, OrderType, Position, Side

__all__ = ["KillSwitch", "RiskManager", "compute_stop", "size_by_risk"]


# --------------------------------------------------------------------------- #
# Kill switch
# --------------------------------------------------------------------------- #
@dataclass
class _KillState:
    day: str = ""                 # ISO date of the current trading day
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    halted: bool = False
    halt_reason: str = ""


class KillSwitch:
    """Persisted, crash-proof global trading halt.

    Halts on: intraday loss >= max_daily_loss_pct, drawdown from peak >=
    max_drawdown_pct, or >= max_consecutive_losses losing trades. State is read
    on startup; if a state file EXISTS but is unreadable, we halt (fail-closed).
    A missing file is a fresh install and initializes cleanly.
    """

    def __init__(self, cfg, state_path: str | Path | None = None, *, autosave: bool = True):
        self.cfg = cfg
        self.path = Path(state_path or cfg.state_file)
        # When autosave is False, routine updates buffer in memory and are flushed
        # once via flush() — so the caller can order the kill-state write AFTER the
        # session write and avoid a half-committed tick. A halt always persists.
        self.autosave = autosave
        self.state = self._load()

    def _load(self) -> _KillState:
        if not self.path.exists():
            return _KillState()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return _KillState(**data)
        except Exception:
            # File is present but corrupt — refuse to trade until a human resets.
            return _KillState(halted=True,
                              halt_reason="kill-switch state unreadable; manual reset required")

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(self.state), fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)   # atomic on POSIX and Windows

    def _save(self) -> None:
        """Persist now unless deferred (autosave=False); see flush()."""
        if self.autosave:
            self._persist()

    def flush(self) -> None:
        """Force-persist buffered state (used by the deferred paper-tick path)."""
        self._persist()

    @property
    def halted(self) -> bool:
        return self.state.halted

    @property
    def halt_reason(self) -> str:
        return self.state.halt_reason

    def halt(self, reason: str) -> None:
        self.state.halted = True
        self.state.halt_reason = reason
        self._persist()

    def reset(self) -> None:
        """Manual operator action: clear the halt and re-baseline."""
        self.state = _KillState()
        self._persist()

    def update_equity(self, equity: float, today: str | None = None) -> None:
        today = today or date.today().isoformat()
        if self.state.day != today or self.state.day_start_equity == 0.0:
            self.state.day = today
            self.state.day_start_equity = equity
        self.state.peak_equity = max(self.state.peak_equity, equity)

        if self.state.day_start_equity > 0:
            daily = equity / self.state.day_start_equity - 1.0
            if daily <= -self.cfg.max_daily_loss_pct:
                self.halt(f"daily loss {daily:.2%} <= -{self.cfg.max_daily_loss_pct:.2%}")
                return
        if self.state.peak_equity > 0:
            dd = equity / self.state.peak_equity - 1.0
            if dd <= -self.cfg.max_drawdown_pct:
                self.halt(f"drawdown {dd:.2%} <= -{self.cfg.max_drawdown_pct:.2%}")
                return
        self._save()

    def record_trade_result(self, pnl: float) -> None:
        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
        if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
            self.halt(f"{self.state.consecutive_losses} consecutive losing trades")
            return
        self._save()


# --------------------------------------------------------------------------- #
# Sizing + stops
# --------------------------------------------------------------------------- #
def compute_stop(entry_price: float, stops_cfg, atr: float | None) -> float | None:
    """Stop price for a long entry. None if a required input is missing."""
    if stops_cfg.method == "atr":
        if atr is None or atr <= 0:
            return None
        return entry_price - stops_cfg.atr_stop_mult * atr
    return entry_price * (1.0 - stops_cfg.stop_loss_pct)


def size_by_risk(equity: float, entry_price: float, stop_price: float,
                 risk_per_trade_pct: float) -> float:
    """Quantity such that (entry - stop) * qty == risk_per_trade_pct * equity."""
    risk_per_unit = entry_price - stop_price
    if risk_per_unit <= 0:
        return 0.0
    return (risk_per_trade_pct * equity) / risk_per_unit


# --------------------------------------------------------------------------- #
# Risk manager
# --------------------------------------------------------------------------- #
class RiskManager:
    def __init__(self, app: AppConfig, risk: RiskConfig, kill_switch: KillSwitch | None = None):
        self.app = app
        self.risk = risk
        self.kill = kill_switch or KillSwitch(risk.kill_switch)
        self._last_order_ts: float | None = None

    def check_order(self, *, symbol: str, side: Side, entry_price: float, equity: float,
                    open_positions: dict[str, Position], gross_exposure_value: float,
                    atr: float | None = None, is_live_order: bool = False,
                    now: float | None = None) -> Decision:
        """Vet (and for entries, size) a proposed order. Returns a Decision."""
        now = time.time() if now is None else now

        # Gate 0: real-order interlock (defense in depth vs the factory/router).
        if is_live_order and not self.app.live_orders_allowed():
            return Decision.reject("live_interlock",
                                   "real orders disabled (mode != live or live.confirm is false)")

        # Exits (SELL of a held position) reduce risk — always allowed, even halted.
        if side == Side.SELL:
            held = open_positions.get(symbol)
            if held is None or held.qty <= 0:
                return Decision.reject("no_position", f"no open position in {symbol} to sell")
            return Decision.allow("exit", Order(symbol, Side.SELL, held.qty, OrderType.MARKET))

        if side != Side.BUY:
            return Decision.reject("bad_side", f"unsupported side {side!r}")

        # Gate 1: kill switch blocks NEW risk.
        if self.kill.halted:
            return Decision.reject("kill_switch", self.kill.halt_reason)

        # Gate 2: throttle.
        if (self._last_order_ts is not None
                and now - self._last_order_ts < self.risk.guards.min_seconds_between_orders):
            return Decision.reject("throttle", "min seconds between orders not elapsed")

        pos, pf, st, gd = (self.risk.position, self.risk.portfolio,
                           self.risk.stops, self.risk.guards)

        # Gate 3: position-count caps.
        held = open_positions.get(symbol)
        if held is not None and held.qty > 0 and pf.max_positions_per_asset <= 1:
            return Decision.reject("max_per_asset", f"already hold {symbol} (no pyramiding)")
        if symbol not in open_positions and len(open_positions) >= pf.max_open_positions:
            return Decision.reject("max_open_positions",
                                   f"already at {pf.max_open_positions} open positions")

        # Gate 4: stop is mandatory.
        stop_price = compute_stop(entry_price, st, atr)
        if st.require_stop_on_entry and (stop_price is None or stop_price >= entry_price):
            return Decision.reject("no_stop", "could not compute a valid protective stop")

        # Gate 5: size by risk, then clamp to every notional cap.
        if pos.sizing == "risk_based":
            qty = size_by_risk(equity, entry_price, stop_price, pos.risk_per_trade_pct)
        else:  # fixed_fraction
            qty = (pos.max_position_pct * equity) / entry_price
        notional = qty * entry_price
        notional = min(notional, pos.max_position_pct * equity, gd.max_order_notional_quote)

        # Gross-exposure + reserve-cash headroom.
        deployable = (1.0 - pf.reserve_cash_pct) * equity
        gross_cap = min(pf.max_gross_exposure_pct * equity, deployable)
        headroom = gross_cap - gross_exposure_value
        if headroom <= 0:
            return Decision.reject("max_gross_exposure", "no exposure headroom left")
        notional = min(notional, headroom)

        if notional < pos.min_position_quote:
            return Decision.reject(
                "below_min", f"sized notional {notional:.2f} < min {pos.min_position_quote}")

        qty = notional / entry_price
        self._last_order_ts = now
        order = Order(symbol, Side.BUY, qty, OrderType.MARKET, stop_price=stop_price)
        return Decision.allow("ok", order,
                              reason=f"sized {qty:.8f} @ ~{entry_price} stop {stop_price:.6f}")
