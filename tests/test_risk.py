"""Tests for the risk layer — the safety system. These are the tests that most
directly protect real money, so they cover the failure modes, not just happy paths."""

from __future__ import annotations

import json

import pytest

from smaoutfits.config import AppConfig, ExchangeCfg, RiskConfig
from smaoutfits.risk import KillSwitch, RiskManager, compute_stop, size_by_risk
from smaoutfits.types import Position, Side


def _app(mode="backtest", confirm=False) -> AppConfig:
    return AppConfig(mode=mode, exchange=ExchangeCfg(
        name="kraken", api_key_env="K", api_secret_env="S"), live={"confirm": confirm})


def _rm(tmp_path, **app_kwargs) -> RiskManager:
    risk = RiskConfig()
    risk.kill_switch.state_file = str(tmp_path / "ks.json")
    ks = KillSwitch(risk.kill_switch)
    return RiskManager(_app(**app_kwargs), risk, ks)


# -- sizing / stops ---------------------------------------------------------
def test_size_by_risk_formula():
    # risk 1% of 10_000 = 100; stop 10 below entry -> 10 units.
    assert size_by_risk(10_000, 100, 90, 0.01) == pytest.approx(10.0)


def test_size_by_risk_zero_when_stop_not_below_entry():
    assert size_by_risk(10_000, 100, 100, 0.01) == 0.0


def test_compute_stop_atr_and_percent():
    class S:  # minimal stops cfg
        method = "atr"
        atr_stop_mult = 2.0
        stop_loss_pct = 0.05

    assert compute_stop(100, S(), atr=5) == pytest.approx(90.0)
    S.method = "percent"
    assert compute_stop(100, S(), atr=None) == pytest.approx(95.0)


# -- kill switch ------------------------------------------------------------
def test_kill_switch_trips_on_daily_loss(tmp_path):
    ks = KillSwitch(RiskConfig().kill_switch, state_path=tmp_path / "ks.json")
    ks.update_equity(1000, today="2026-01-01")
    assert not ks.halted
    ks.update_equity(950, today="2026-01-01")   # -5% > 4% daily limit
    assert ks.halted and "daily loss" in ks.halt_reason


def test_kill_switch_trips_on_consecutive_losses(tmp_path):
    ks = KillSwitch(RiskConfig().kill_switch, state_path=tmp_path / "ks.json")
    for _ in range(5):
        ks.record_trade_result(-1.0)
    assert ks.halted


def test_kill_switch_survives_restart(tmp_path):
    path = tmp_path / "ks.json"
    ks = KillSwitch(RiskConfig().kill_switch, state_path=path)
    ks.update_equity(1000, today="2026-01-01")
    ks.update_equity(950, today="2026-01-01")
    assert ks.halted
    # A brand new instance (simulated restart) must still see the halt.
    ks2 = KillSwitch(RiskConfig().kill_switch, state_path=path)
    assert ks2.halted


def test_kill_switch_fail_closed_on_corrupt_state(tmp_path):
    path = tmp_path / "ks.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    ks = KillSwitch(RiskConfig().kill_switch, state_path=path)
    assert ks.halted  # corrupt existing state => refuse to trade


def test_kill_switch_fresh_install_not_halted(tmp_path):
    ks = KillSwitch(RiskConfig().kill_switch, state_path=tmp_path / "missing.json")
    assert not ks.halted


def test_kill_switch_manual_reset(tmp_path):
    path = tmp_path / "ks.json"
    ks = KillSwitch(RiskConfig().kill_switch, state_path=path)
    ks.halt("test")
    assert ks.halted
    ks.reset()
    assert not ks.halted


# -- risk manager gates -----------------------------------------------------
def test_buy_is_sized_and_allowed(tmp_path):
    rm = _rm(tmp_path)
    d = rm.check_order(symbol="BTC/USD", side=Side.BUY, entry_price=100.0, equity=10_000,
                       open_positions={}, gross_exposure_value=0.0, atr=4.0)
    assert d.allowed and d.order is not None
    assert d.order.qty > 0 and d.order.stop_price is not None and d.order.stop_price < 100.0


def test_no_naked_entry_when_stop_uncomputable(tmp_path):
    rm = _rm(tmp_path)
    # ATR method but no ATR provided -> no stop -> reject (require_stop_on_entry).
    d = rm.check_order(symbol="BTC/USD", side=Side.BUY, entry_price=100.0, equity=10_000,
                       open_positions={}, gross_exposure_value=0.0, atr=None)
    assert not d.allowed and d.rule == "no_stop"


def test_kill_switch_blocks_buys_but_not_sells(tmp_path):
    rm = _rm(tmp_path)
    rm.kill.halt("test halt")
    buy = rm.check_order(symbol="BTC/USD", side=Side.BUY, entry_price=100.0, equity=10_000,
                         open_positions={}, gross_exposure_value=0.0, atr=4.0)
    assert not buy.allowed and buy.rule == "kill_switch"
    # An exit of a held position is still allowed while halted (it reduces risk).
    sell = rm.check_order(symbol="BTC/USD", side=Side.SELL, entry_price=100.0, equity=10_000,
                          open_positions={"BTC/USD": Position("BTC/USD", 1.0, 90.0)},
                          gross_exposure_value=100.0, atr=4.0)
    assert sell.allowed and sell.rule == "exit"


def test_max_open_positions_enforced(tmp_path):
    rm = _rm(tmp_path)
    held = {f"C{i}/USD": Position(f"C{i}/USD", 1.0, 10.0) for i in range(4)}
    d = rm.check_order(symbol="BTC/USD", side=Side.BUY, entry_price=100.0, equity=10_000,
                       open_positions=held, gross_exposure_value=40.0, atr=4.0)
    assert not d.allowed and d.rule == "max_open_positions"


def test_no_pyramiding(tmp_path):
    rm = _rm(tmp_path)
    held = {"BTC/USD": Position("BTC/USD", 1.0, 90.0)}
    d = rm.check_order(symbol="BTC/USD", side=Side.BUY, entry_price=100.0, equity=10_000,
                       open_positions=held, gross_exposure_value=100.0, atr=4.0)
    assert not d.allowed and d.rule == "max_per_asset"


def test_gross_exposure_cap(tmp_path):
    rm = _rm(tmp_path)
    # Already at the 40% gross cap on $10k = $4000 deployed.
    d = rm.check_order(symbol="BTC/USD", side=Side.BUY, entry_price=100.0, equity=10_000,
                       open_positions={"X/USD": Position("X/USD", 1, 1)},
                       gross_exposure_value=4000.0, atr=4.0)
    assert not d.allowed and d.rule == "max_gross_exposure"


def test_live_interlock_blocks_real_orders_unless_confirmed(tmp_path):
    # mode != live -> a "live" order is refused even though sizing would pass.
    rm = _rm(tmp_path, mode="backtest")
    d = rm.check_order(symbol="BTC/USD", side=Side.BUY, entry_price=100.0, equity=10_000,
                       open_positions={}, gross_exposure_value=0.0, atr=4.0, is_live_order=True)
    assert not d.allowed and d.rule == "live_interlock"

    # mode == live but confirm == false -> still refused.
    rm2 = _rm(tmp_path, mode="live", confirm=False)
    d2 = rm2.check_order(symbol="BTC/USD", side=Side.BUY, entry_price=100.0, equity=10_000,
                         open_positions={}, gross_exposure_value=0.0, atr=4.0, is_live_order=True)
    assert not d2.allowed and d2.rule == "live_interlock"

    # mode == live AND confirm == true -> allowed.
    rm3 = _rm(tmp_path, mode="live", confirm=True)
    d3 = rm3.check_order(symbol="BTC/USD", side=Side.BUY, entry_price=100.0, equity=10_000,
                         open_positions={}, gross_exposure_value=0.0, atr=4.0, is_live_order=True)
    assert d3.allowed


def test_below_min_position_rejected(tmp_path):
    rm = _rm(tmp_path)
    # Tiny equity -> risk-based size is below the $25 minimum.
    d = rm.check_order(symbol="BTC/USD", side=Side.BUY, entry_price=100.0, equity=50,
                       open_positions={}, gross_exposure_value=0.0, atr=4.0)
    assert not d.allowed and d.rule == "below_min"


def test_state_file_is_valid_json_after_persist(tmp_path):
    path = tmp_path / "ks.json"
    ks = KillSwitch(RiskConfig().kill_switch, state_path=path)
    ks.update_equity(1000, today="2026-01-01")
    json.loads(path.read_text(encoding="utf-8"))  # must parse
