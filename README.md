# SMA-outfits — a moving-average trading framework (backtest → paper → live)

A Python framework for **systematically testing and trading moving-average
strategies**, built for Kraken (crypto, now) with a broker-abstraction layer so
it can target Webull (US equities) later.

## What this is — and what it is not

This project started from the [`unfairmarket/SMA-outfits`](https://github.com/unfairmarket/SMA-outfits)
document (preserved here at [`docs/SOURCE_ANALYSIS.md`](docs/SOURCE_ANALYSIS.md)).
That document is a prose write-up — it ships **no code and no data**. Its
central claim is that intelligence agencies secretly steer the entire equity
market using specific moving-average period sets ("outfits") chosen for
numerological reasons (presidential-seat numbers, a 9/11 "homage",
Waring's-problem integers, and so on).

We keep the **method** and discard the **mythology**:

- **Kept (sound):** moving-average crossover and multi-MA trend strategies are a
  real, classic, backtestable technique. Building, backtesting, and risk-managing
  them is legitimate and useful.
- **Discarded (unfounded):** the idea that a 911-period or "47th-president" MA is
  secretly predictive. There is no evidence for it. So we **test** the source's
  exact "outfits" empirically instead of trusting them — if any set shows a real,
  repeatable edge over buy-and-hold, the backtester will show it; if not, that's
  the answer.

**This is not financial advice.** Most retail algorithmic strategies underperform
buy-and-hold after fees. The value here is a disciplined process. Nothing trades
real money until it has passed backtesting **and** forward paper-trading.

## Status

Working backtest + research engine, fully tested (44 tests, lint-clean). Built so far:

- **Indicators, data layer, strategies, backtester** — verified end-to-end on live Kraken data.
- **Empirical outfit study** — the headline deliverable. See [`docs/OUTFIT_STUDY.md`](docs/OUTFIT_STUDY.md).
  TL;DR: the outfits show **no edge over random period sets**, and the numerology
  ones are if anything slightly worse. Their only effect is generic trend-following
  drawdown avoidance.
- **Risk layer** — crash-proof, fail-closed kill switch; risk-based sizing with
  mandatory stops; gross-exposure / position caps; a triple-checked "no live
  orders unless explicitly confirmed" interlock.

Architecture + interfaces: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Decisions
log: [`docs/DECISIONS.md`](docs/DECISIONS.md).

- **Live `KrakenBroker`** — built and proven against a real account in **validate-only**
  mode (auth, balances, OHLCV, and a `validate=true` order Kraken accepted without
  placing). Real orders stay locked behind `allow_live` / `live.confirm`.

**Next (needs your go-ahead):** real trading needs USD funding and an explicit decision
to flip `live.confirm` — and ideally a strategy you actually believe in (not the
debunked outfits). Until then nothing trades for real.

## Layout

```
config/    config.example.yaml, outfits.yaml, universe.yaml, risk.yaml
src/smaoutfits/   the Python package (engine, strategies, brokers, risk, data)
tests/     unit tests
data/      local OHLCV cache + run state (gitignored)
docs/      SOURCE_ANALYSIS.md (provenance) + ARCHITECTURE.md (the real design)
notebooks/ exploratory backtest notebooks
```

## Setup

```bash
python -m venv .venv
# Windows:  .venv\Scripts\Activate.ps1
# *nix:     source .venv/bin/activate
pip install -r requirements.txt

cp config/config.example.yaml config/config.yaml   # then edit
```

API keys go in a `.env` file (gitignored), never in committed config:

```
KRAKEN_API_KEY=your_key
KRAKEN_API_SECRET=your_secret
```

For Kraken, create a key in **Settings → API** with **query** + **trade**
permissions (you do **not** need withdrawal permission — leave it off).

## Roadmap

1. ✅ **Data + backtester** — pull OHLCV, run any outfit on any symbol, report vs buy-and-hold.
2. ✅ **Empirical outfit study** — backtest the outfits across the crypto universe; rank honestly.
3. ✅ **Risk layer** — risk-based sizing, mandatory stops, crash-proof daily-loss/drawdown kill switch.
4. 🟦 **Broker abstraction + paper trading** — done: portfolio ledger, `SimulatedBroker`, engine, CLI, paper trading on Kraken's public feed (fake money, no keys). Remaining: live `KrakenBroker` (python-kraken-sdk) — needs your Kraken API keys.
5. 🟦 **Live broker** — `KrakenBroker` built and proven in **validate-only** mode against a real account (`python -m smaoutfits check-kraken`). Real orders still OFF — need USD funding, `live.confirm`, and your explicit go-ahead.
6. ⬜ **Webull adapter** — once your account is verified and you have OpenAPI access (official UAT sandbox needs no approval).

## License

Apache-2.0 (inherited from the source repo). See [`LICENSE`](LICENSE).
