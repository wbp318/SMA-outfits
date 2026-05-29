"""Tests that the shipped config files parse into the typed models."""

from __future__ import annotations

from pathlib import Path

import pytest

from smaoutfits.config import (
    load_app_config,
    load_outfits,
    load_risk_config,
    load_universe,
)

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def test_app_config_example_parses():
    cfg = load_app_config(CONFIG_DIR / "config.example.yaml")
    assert cfg.mode == "backtest"
    assert cfg.exchange.name == "kraken"
    # Live orders must be disallowed by default in the example config.
    assert cfg.live_orders_allowed() is False


def test_risk_config_parses_with_sane_defaults():
    risk = load_risk_config(CONFIG_DIR / "risk.yaml")
    assert 0 < risk.position.risk_per_trade_pct <= 0.05
    assert risk.kill_switch.max_daily_loss_pct > 0
    assert risk.portfolio.max_open_positions >= 1


def test_outfits_parse_completely():
    outfits = load_outfits(CONFIG_DIR / "outfits.yaml")
    # The source defines 25 outfits and 3 "systems".
    assert len(outfits.outfits) == 25
    assert len(outfits.systems) == 3
    # The classic 10/50/200 must be present and flagged non-numerology.
    sp = outfits.by_id("sp500")
    assert sp.periods == [10, 50, 200]
    assert sp.numerology is False
    # The 9/11 "homage" set is present and flagged as numerology.
    wtc = outfits.by_id("wtc_911")
    assert wtc.numerology is True
    # Every system references a real outfit.
    for system in outfits.systems:
        outfits.by_id(system.outfit_id)


def test_universe_parses():
    uni = load_universe(CONFIG_DIR / "universe.yaml")
    assert "BTC/USD" in uni.crypto_kraken
    assert "ETH/USD" in uni.crypto_kraken
    # Equities are Webull-later and must not leak into the Kraken list.
    assert "AAPL" not in uni.crypto_kraken


def test_extra_field_is_rejected():
    # extra="forbid" should catch typo'd keys.
    from pydantic import ValidationError

    from smaoutfits.config import RiskConfig

    with pytest.raises(ValidationError):
        RiskConfig.model_validate({"position": {"sizing": "risk_based"}, "bogus": 1})
