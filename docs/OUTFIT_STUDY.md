# Empirical outfit study — results

**Question:** Do the source repo's SMA "outfits" actually beat buy-and-hold
after fees? And are the named/numerology outfits (911, presidential seats,
Waring's problem, etc.) special compared to *random* period sets?

**Short answer: No, and no.** On the data we can get, the outfits show no edge
over random period sets, and the numerology outfits are if anything slightly
*worse* than the non-numerology ones. Their only measurable effect is generic
trend-following drawdown avoidance — which any moving-average filter, including
random ones, reproduces.

Reproduce with: `PYTHONPATH=src python -m smaoutfits.study`
Raw data: `backtest_results/outfit_study_raw.csv` (3,060 rows).

## How it was tested

- **Universe:** 10 liquid Kraken crypto pairs (BTC, ETH, SOL, XRP, ADA, DOGE,
  LTC, LINK, DOT, AVAX vs USD).
- **Timeframes:** 1d, 4h, 1h.
- **Rule per outfit:** long when the shortest-period SMA is above the
  longest-period SMA, else flat (spot, long/flat only — no shorting). One fixed,
  consistent rule across all outfits so they're comparable.
- **No lookahead:** signals are acted on the *next* bar.
- **Costs:** 0.26% fee + 0.05% slippage per trade.
- **Out-of-sample:** scored on a held-out final 30% of history (positions are
  warmed on the full series, then evaluated only on the test window).
- **Control:** 25 *random* outfits (periods ≤ 300 so they form on the available
  history) run identically — the baseline an "arbitrary trend filter" achieves.

## Results (held-out test window)

| Timeframe | Buy & hold return | Outfit beat-rate vs B&H | **Random** beat-rate vs B&H | Outfit mean return | Random mean return |
|-----------|------------------:|------------------------:|----------------------------:|-------------------:|-------------------:|
| 1d | −51.4% | 1.000 | **0.992** | −8.2% | −15.2% |
| 1h | −4.5% | 0.877 | **0.900** | −1.2% | −1.0% |
| 4h | −4.3% | 0.457 | **0.572** | −5.5% | −4.3% |

*Beat-rate = fraction of (strategy, symbol) pairs whose test return beat that
symbol's buy-and-hold.*

Fraction of runs that were actually **profitable** (positive test return):

| | Named outfits | Random outfits | Buy & hold |
|---|---:|---:|---:|
| Positive-return rate | **3.9%** | 6.0% | 3.3% |

Numerology vs non-numerology named outfits (mean test return): **−5.4%** vs
**−4.2%**. The "magic numbers" did slightly *worse*.

## What this means

1. **No special edge.** The named outfits' beat-rate over buy-and-hold is
   essentially identical to (1d), or *lower* than (1h, 4h), random period sets.
   If secret, meaningful numbers existed, the named outfits would clearly beat
   random ones. They don't.

2. **The only effect is drawdown avoidance.** On the 1d test window crypto fell
   ~51%; the trend filters sat in cash for most of it (median outfit return ≈
   0%), so they "beat" buy-and-hold by *not losing*, not by making money. That's
   textbook trend-following — and random filters do it just as well.

3. **In choppy regimes they lose.** On 4h, the filters got whipsawed and
   underperformed buy-and-hold more than half the time, with random sets again
   doing better than the named ones.

4. **Numerology adds nothing.** 911, presidential-seat numbers, and Waring's
   integers are not better — they're marginally worse — than ordinary numbers.

**Bottom line:** there is no empirical basis for trading these specific outfits
expecting an edge. The *framework* (systematic MA strategies, backtested with
fees, risk-managed) is sound and worth keeping; the *specific numbers* from the
source are not.

## Honest caveats (why this isn't the final word)

- **Limited history.** Kraken's public OHLC serves ~720 bars max, so **only 7 of
  25 outfits could even form** (the rest need 730–999 bars). The large-period
  outfits are untested here — not vindicated, just unmeasured. Deeper history
  (Kraken Trades-rebuild or an external source) would let us test them.
- **One regime.** The test window is a single ~2-year span dominated by a
  downturn. One regime is not a robust verdict; walk-forward across many
  windows (planned) is stronger.
- **One rule interpretation.** We used shortest-vs-longest crossover. Other
  readings (the source's specific fast/slow pairs, full-ribbon stacking) exist
  and are implemented (`SystemsStrategy`, `StackedOutfitStrategy`) for follow-up.
- **Crypto, not equities.** The source describes equities; we tested crypto
  (what's tradable on Kraken today). The source claims the outfits are universal
  across asset classes, so this is a fair test — but equities (via Webull later)
  would be a direct one.
- **Long/flat spot only.** No shorting; the source's "automated short" behavior
  isn't modeled here.

These caveats mean "untested," not "promising." Nothing here suggests the
numbers carry hidden edge — the burden of proof is on the claim, and it failed
every test we *could* run.
