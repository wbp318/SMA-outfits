# Changelog

All notable changes to this project are documented here.

## [0.4.0] — 2026-05-29

Forward paper trading, wired to the README's own strategy. Still fake money.

### Added
- **`paper-live` CLI** — a forward paper session that steps the latest **closed**
  bar (drops Kraken's still-forming candle so it never acts on partial data),
  persists cash/positions/equity to `data/` (atomic write, **resumable**), and is
  **idempotent per bar** (won't double-count). Survives transient network errors
  so a long `--poll` run keeps going. `--once`, `--poll`, `--iterations`, `--reset`.
- **`PaperSession`** (`paper_session.py`) — persistent forward-session state.
- **README strategy selection** — `--system spx_system|ixic_system|dji_system`
  runs the source's "systems" (10/50/200, 20/100/250, 30/60/90/300/600/900 trend
  rules). `paper-live` defaults to the README's flagship **10/50/200 "System"**.
- 4 more tests (63 total), lint-clean.

### Usage
```bash
# Paper-trade the README's 10/50/200 System on BTC/USD 30m, one tick:
python -m smaoutfits paper-live --system spx_system --symbol BTC/USD --tf 30m --once
# Then schedule it once per bar (Task Scheduler / cron / Claude /loop) to forward-test live.
```

### Notes
- Still paper-only (`SimulatedBroker`, no real orders). The README strategy is run
  here because you asked to — the v0.1.0 study still stands: it has no measured edge,
  and paper is the right place to watch that play out at zero risk.

## [0.3.0] — 2026-05-29

Live Kraken broker — built and **proven safe** against a real account in
validate-only mode. Still places no real orders.

### Added
- **`KrakenBroker`** (`broker_kraken.py`) via `python-kraken-sdk`: balances, OHLCV,
  asset-pair/precision mapping (`BTC/USD` ↔ `XBTUSD`), a `validate_order` dry-run
  (`validate=true`, places nothing), and a `submit` that **refuses to place a real
  order unless `allow_live=True`**.
- `make_broker` now returns `KrakenBroker` for `mode=live` + `live.confirm` (still gated).
- **CLI `check-kraken`** — read-only + validate-only connectivity check (no real order).
- Mocked `KrakenBroker` tests, including the "submit refuses without `allow_live`" guard. 59 tests.

### Verified
- Ran end-to-end against a real Kraken account in **validate-only** mode: auth +
  balances, OHLCV pull, and a `validate=true` market order that Kraken accepted
  **without placing anything**.

### Still off
- **No real orders are placed.** Real trading needs `mode=live` + `live.confirm=true`
  and, practically, USD funding (risk caps assume a small account).

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
