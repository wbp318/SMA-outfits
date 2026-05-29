# Changelog

All notable changes to this project are documented here.

## [0.2.0] — 2026-05-29

Paper-trading layer: the engine and broker abstraction that let you forward-test
any strategy with fake money on Kraken's public feed. Still no real orders.

### Added
- **Portfolio ledger** (`portfolio.py`): cash, positions, mark-to-market equity, realized PnL.
- **Broker abstraction** (`broker.py`): `Broker` protocol + `SimulatedBroker` (fees + slippage);
  `make_broker` factory enforcing the no-live-orders gate (#1).
- **Engine** (`engine.py`): one event loop shared by backtest replay and paper trading —
  strategy → risk → broker → portfolio → kill switch, identical code path for paper and live.
- **CLI** (`python -m smaoutfits {study,backtest,paper}`): stdlib argparse, no extra deps.
  Paper trading runs on Kraken's PUBLIC feed — no API keys, no real orders.
- 9 more tests (53 total), still lint-clean.

### Notes
- The live `KrakenBroker` is still pending (needs your Kraken API keys + an explicit
  go-ahead). Paper is validated first; live order placement stays locked behind `live.confirm`.

## [0.1.0] — 2026-05-29

First milestone: a tested moving-average **research + backtest framework**, plus
an honest empirical study of the source repo's "outfits". No live trading.

### Added
- **Indicator core** (`indicators.py`): SMA, multi-MA, trend regime, crossover,
  key-level breach, ATR — pure, no-lookahead, unit-tested.
- **Data layer** (`data.py`): `ccxt`-based OHLCV fetch with on-disk cache and a
  CSV import escape hatch; Kraken's ~720-bar history limit documented.
- **Strategy layer** (`strategy.py`): crossover, multi-MA "outfit", stacked-ribbon,
  and the 3 "systems" trend rules — long/flat (spot, no shorting).
- **Backtester** (`backtest.py`): vectorized, next-bar (no-lookahead) execution,
  fees + slippage, annualized metrics vs buy-and-hold. Doubles as the independent
  reconciliation oracle for the future vectorbt sweep.
- **Empirical outfit study** (`study.py`, [`docs/OUTFIT_STUDY.md`](docs/OUTFIT_STUDY.md)):
  all 25 outfits across 10 Kraken pairs × {1d, 4h, 1h}, out-of-sample, with 25
  random period-sets as a control.
- **Risk layer** (`risk.py`): crash-proof, fail-closed kill switch (daily-loss /
  drawdown / consecutive-loss halts, survives restarts); risk-based position
  sizing with mandatory stops; gross-exposure and position-count caps; a
  triple-checked "no live orders unless `live.confirm`" interlock.
- **Config** (`config.py` + `config/*.yaml`): validated pydantic models;
  survival-first risk defaults. **Architecture** in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
  **decisions** in [`docs/DECISIONS.md`](docs/DECISIONS.md).
- 44 tests, lint-clean (ruff).

### Findings
- The SMA "outfits" show **no edge over random period sets**; the numerology
  outfits performed slightly *worse* than ordinary ones. Their only measurable
  effect is generic trend-following drawdown avoidance, which any moving average
  reproduces. See [`docs/OUTFIT_STUDY.md`](docs/OUTFIT_STUDY.md).

### Not included
- No live/paper broker integration, no connection to any real exchange, and no
  real orders. Live trading is locked behind the `live.confirm` config flag.

### Provenance
- Forked from [`unfairmarket/SMA-outfits`](https://github.com/unfairmarket/SMA-outfits);
  the original document is preserved at [`docs/SOURCE_ANALYSIS.md`](docs/SOURCE_ANALYSIS.md).
