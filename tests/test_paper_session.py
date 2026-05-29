"""Tests for persistent paper-session state."""

from __future__ import annotations

import json

import pytest

from smaoutfits.paper_session import PaperSession
from smaoutfits.types import Fill, Side


def test_fresh_session_defaults(tmp_path):
    s = PaperSession.load(tmp_path / "s.json", symbol="BTC/USD", initial_cash=1000.0)
    assert s.cash == 1000.0
    assert s.last_bar_ts is None
    assert s.positions == {}


def test_round_trip_persists_position_and_bar(tmp_path):
    path = tmp_path / "s.json"
    s = PaperSession.load(path, symbol="BTC/USD", initial_cash=1000.0)
    pf = s.to_portfolio()
    pf.apply_fill(Fill("BTC/USD", Side.BUY, qty=0.01, price=50_000.0, fee=1.3))
    s.absorb(pf, "2026-01-01T00:00:00+00:00", equity=999.0)
    s.save(path)

    again = PaperSession.load(path, symbol="BTC/USD", initial_cash=1000.0)
    assert again.last_bar_ts == "2026-01-01T00:00:00+00:00"
    assert again.already_processed("2026-01-01T00:00:00+00:00")
    assert again.equity_curve[-1] == ["2026-01-01T00:00:00+00:00", 999.0]
    pf2 = again.to_portfolio()
    assert pf2.positions["BTC/USD"].qty == pytest.approx(0.01)
    assert pf2.cash == pytest.approx(1000.0 - 0.01 * 50_000.0 - 1.3)


def test_saved_file_is_valid_json(tmp_path):
    path = tmp_path / "s.json"
    PaperSession.load(path, symbol="ETH/USD", initial_cash=500.0).save(path)
    json.loads(path.read_text(encoding="utf-8"))


def test_absorb_accumulates_fill_count(tmp_path):
    s = PaperSession.load(tmp_path / "s.json", symbol="BTC/USD", initial_cash=1000.0)
    pf = s.to_portfolio()
    pf.apply_fill(Fill("BTC/USD", Side.BUY, 0.01, 50_000.0, fee=0.0))
    s.absorb(pf, "2026-01-01T00:00:00+00:00", 1000.0)
    assert s.n_fills == 1
