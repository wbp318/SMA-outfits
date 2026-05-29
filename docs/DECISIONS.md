# Decisions log

Defaults taken autonomously (auto mode). Anything here is cheap to change —
tell me and I'll adjust. Live trading is the one thing that stays OFF until
William explicitly approves; it is also structurally gated in code.

| # | Open question | Decision (default) | Revisit at |
|---|---------------|--------------------|-----------|
| 1 | Overwrite `config/risk.yaml` with the tighter "survival-first" schema? | **Yes, but later** — adopt the reconciled schema (volatility-target sizing, 0.75%/trade, 40% gross, daily-loss 4%, drawdown 15%, order guards, venue tier, live-safety) when building the risk layer (STEP 7), and expand the pydantic model with it then. Kept the simpler committed version for now so config tests stay green. | STEP 7 |
| 2 | Live go/no-go + real starting capital | **Deferred.** Live stays OFF (`live.confirm: false`). Risk caps assume a ~$3,000 survival-first account; re-tune absolute caps to real equity before go-live. | STEP 11 (William decision required) |
| 3 | Kraken API tier (throttle limit) | Default **intermediate (125)**. | STEP 9 |
| 4 | Deep backtest history (Kraken REST OHLC caps at ~720 candles) | Use **ccxt data-only backfill** for now (design-approved). Flag which outfits can't form given available history. Add Kraken Trades-rebuild / CSV import for deep history later. | STEP 4 |
| 5 | Which timeframes for the crypto sweep | **`1d`, `4h`, `1h`.** `1d` gives the most history (~720 days). Large-period outfits (300/600/900/976) cannot form on ~720 bars — reported as "insufficient history", which is itself an honest finding. | STEP 4–5 |
| 6 | Crypto correlation groups | majors=[BTC,ETH]; l1_alts=[SOL,ADA,AVAX,DOT]; payments_other=[XRP,LTC,DOGE,LINK]. | STEP 7 |
| 7 | Exchange-native stops vs software trailing | **Exchange-native stops** (protect capital even if the bot is offline); simulated in paper/backtest. | STEP 7/10 |
| 8 | `backtesting.py` (AGPL) optional cross-check | Keep as **optional dev-only** dependency, never on the live path / never network-exposed. | STEP 4 (optional) |

## Deviations from the workflow's file layout

The synthesized design proposed ~30 source modules with deep package nesting
(`config/`, `data/`, `strategy/`, `risk/`, `broker/`, `engine/`, `portfolio/`).
To avoid premature fragmentation we start **flat** (`indicators.py`, `config.py`,
`data.py`, `strategy.py`, `backtest.py`, `study.py`) and only split into packages
where there are genuinely many cohesive submodules (the **risk** and **broker**
layers). Same architecture, fewer 20-line files. We also build a correct NumPy
backtester first (it doubles as vectorbt's reconciliation oracle and works even
where `vectorbt`/`numba` wheels lag a new Python release).
