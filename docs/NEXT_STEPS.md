# Next session — scheduled forward paper trading

**Goal:** run the forward paper session automatically once per closed bar so it
forward-tests the README strategy (and any others) over real time with fake money,
accumulating an equity curve in `data/`. Still paper-only — no real orders.

Everything below is build-next-session; nothing here is started yet.

## Why this is the right next step
`paper-live --once` already does one correct, idempotent, resumable tick (steps the
latest *closed* bar, persists state). What's missing is **running it on a cadence**
over days/weeks and an easy way to **review the results**.

## Plan

1. **Wrapper script** — `scripts/paper_tick.ps1` (PowerShell) that:
   - `cd` to the repo, sets `PYTHONPATH=src`,
   - runs `.venv\Scripts\python.exe -m smaoutfits paper-live --once --system spx_system --symbol BTC/USD --tf 30m`,
   - appends stdout to `data/paper_tick.log`.
   - Keep it parameterized (symbol/system/tf as script args) so we can schedule several.

2. **Schedule it** — Windows Task Scheduler via `schtasks`:
   - Run the wrapper every 30 min (matching the 30m timeframe), a couple minutes
     after the bar close (e.g. at :02 and :32) so the candle is settled.
   - Idempotency already guards against double-runs / catch-up runs (it skips a bar
     it has already processed), so missed/overlapping fires are safe.
   - Decide: one task per (symbol, timeframe, strategy), or one script that loops a list.

3. **Reporting** — add a `paper-report` CLI command (or a small notebook) that reads
   `data/paper_<symbol>.json` and prints/plots: equity curve, return vs buy-and-hold
   over the same window, # trades, current position, and kill-switch status.
   - Reuse `backtest.buy_and_hold` for the benchmark over the session's date range.

4. **(Optional) multi-strategy** — run the README systems (`spx_system`, `ixic_system`,
   `dji_system`) and/or a couple of outfits side by side in separate session files,
   so we can compare them live, head-to-head, over the same period.

5. **Let it run, then review** — after it's collected a week or two of bars, compare
   the live paper equity curves against the v0.1.0 study's conclusion (expect: no edge;
   mostly flat / drawdown-avoidance). This is the real-time confirmation of the study.

## Decisions to make next session
- Cadence + which symbol(s)/timeframe(s)/strategy(ies) to run.
- Task Scheduler (survives reboots, runs unattended) vs Claude `/loop` (only while a
  session is open). Task Scheduler is the better fit for an unattended forward test.
- Whether to keep it to BTC/USD 30m (README's SPX-system timeframe) or add 1h/4h.

## Reminders / guardrails (already in place)
- Paper-only; `SimulatedBroker`; live stays locked behind `live.confirm`.
- Data layer returns **closed bars only**; engine uses a one-bar lag (matches backtest).
- Kill switch (`data/kill_switch_paper.json`) is armed; `--reset` wipes a session.
- Honest expectation: the v0.1.0 study found the outfits have **no edge** — this
  forward test is to *watch* that at zero risk, not because we expect it to print money.
