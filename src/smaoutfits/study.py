"""Empirical outfit study — the project's first real deliverable.

Question: does any SMA "outfit" actually beat buy-and-hold after fees, and are
the named/numerology outfits special compared to *random* period sets?

Method: sweep every outfit across the crypto universe and several timeframes,
score on a held-out test window (not the data the rule was eyeballed on), and
include N random outfits as a control. If the named outfits don't beat random
outfits, the "secret numbers" thesis has no empirical support — which is exactly
what we want to know before risking a cent.

Run:  python -m smaoutfits.study
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import evaluate_target
from .config import OutfitsConfig, Universe, load_outfits, load_universe
from .data import MarketData
from .strategy import OutfitStrategy

DEFAULT_TIMEFRAMES = ["1d", "4h", "1h"]
RESULTS_DIR = Path("backtest_results")


def random_outfit(rng: np.random.Generator, k: int = 6, lo: int = 2, hi: int = 300) -> list[int]:
    """A random sorted set of distinct MA periods — a control 'outfit'.

    ``hi`` is capped at 300 (not 999) so the control sets actually form on the
    ~720 bars Kraken's public OHLC serves; otherwise every random outfit is
    'insufficient history' and the control is empty. These are the fair baseline
    for 'an arbitrary short/mid MA trend filter' that the named outfits must beat.
    """
    return sorted(int(p) for p in rng.choice(range(lo, hi + 1), size=k, replace=False))


def _record(rows: list[dict], *, name: str, kind: str, numerology, symbol: str,
            timeframe: str, target: pd.Series, close: pd.Series, split: int,
            formed: bool, fee_pct: float, slippage_pct: float) -> None:
    windows = {
        "full": (target, close),
        "test": (target.iloc[split:], close.iloc[split:]),
    }
    for window, (tgt, cls) in windows.items():
        res = evaluate_target(name, timeframe, tgt, cls,
                              fee_pct=fee_pct, slippage_pct=slippage_pct, formed=formed)
        row = res.as_row()
        row.update(symbol=symbol, window=window, kind=kind, numerology=numerology)
        rows.append(row)


def run_study(outfits_cfg: OutfitsConfig, universe: Universe, md: MarketData, *,
              timeframes: list[str] = DEFAULT_TIMEFRAMES, fee_pct: float = 0.0026,
              slippage_pct: float = 0.0005, max_bars: int = 720,
              holdout_frac: float = 0.30, n_random: int = 25, seed: int = 7) -> pd.DataFrame:
    """Return a tidy DataFrame: one row per (strategy, symbol, timeframe, window)."""
    rng = np.random.default_rng(seed)
    random_sets = [random_outfit(rng) for _ in range(n_random)]
    rows: list[dict] = []

    for tf in timeframes:
        for symbol in universe.crypto_kraken:
            try:
                df = md.fetch_ohlcv(symbol, tf, max_bars=max_bars)
            except Exception as exc:  # one unlisted/odd symbol shouldn't kill the sweep
                print(f"  ! skip {symbol} {tf}: {type(exc).__name__}: {exc}")
                continue
            if len(df) < 40:
                print(f"  ! skip {symbol} {tf}: only {len(df)} bars")
                continue
            close = df["close"]
            split = int(len(df) * (1 - holdout_frac))

            # Buy-and-hold benchmark (the bar to beat).
            _record(rows, name="buy_and_hold", kind="benchmark", numerology=None,
                    symbol=symbol, timeframe=tf, target=pd.Series(1.0, index=df.index),
                    close=close, split=split, formed=True,
                    fee_pct=fee_pct, slippage_pct=slippage_pct)

            # Named outfits.
            for outfit in outfits_cfg.outfits:
                strat = OutfitStrategy(periods=list(outfit.periods))
                formed = len(df) > strat.warmup_bars() + 1
                _record(rows, name=outfit.id, kind="outfit", numerology=outfit.numerology,
                        symbol=symbol, timeframe=tf, target=strat.target_position(df),
                        close=close, split=split, formed=formed,
                        fee_pct=fee_pct, slippage_pct=slippage_pct)

            # Random control outfits.
            for i, periods in enumerate(random_sets):
                strat = OutfitStrategy(periods=periods)
                formed = len(df) > strat.warmup_bars() + 1
                _record(rows, name=f"random_{i:02d}", kind="random", numerology=None,
                        symbol=symbol, timeframe=tf, target=strat.target_position(df),
                        close=close, split=split, formed=formed,
                        fee_pct=fee_pct, slippage_pct=slippage_pct)

    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """Per-timeframe headline: do outfits beat B&H, and do they beat random?

    Beat-rate is computed per (strategy, symbol): a strategy 'beats' if its
    held-out test return exceeds that same symbol's buy-and-hold test return.
    Only rows where the strategy could fully form (enough history) are counted.
    """
    test = df[(df["window"] == "test") & (df["formed"])].copy()
    bh = (df[(df["window"] == "test") & (df["kind"] == "benchmark")]
          .set_index(["timeframe", "symbol"])["total_return"])

    test["bh_return"] = test.apply(
        lambda r: bh.get((r["timeframe"], r["symbol"]), np.nan), axis=1)
    test["beats_bh"] = test["total_return"] > test["bh_return"]

    out = []
    for tf, g in test.groupby("timeframe"):
        outfits = g[g["kind"] == "outfit"]
        randoms = g[g["kind"] == "random"]
        bh_mean = g[g["kind"] == "benchmark"]["total_return"].mean()
        out.append({
            "timeframe": tf,
            "bh_mean_test_return": round(float(bh_mean), 4),
            "outfit_beat_rate": round(float(outfits["beats_bh"].mean()), 3),
            "random_beat_rate": round(float(randoms["beats_bh"].mean()), 3),
            "outfit_median_test_return": round(float(outfits["total_return"].median()), 4),
            "random_median_test_return": round(float(randoms["total_return"].median()), 4),
            "outfit_obs": int(len(outfits)),
            "random_obs": int(len(randoms)),
        })
    return pd.DataFrame(out)


def top_outfits(df: pd.DataFrame, timeframe: str, n: int = 8) -> pd.DataFrame:
    test = df[(df["window"] == "test") & (df["formed"]) &
              (df["kind"] == "outfit") & (df["timeframe"] == timeframe)]
    if test.empty:
        return test
    agg = (test.groupby("name")
           .agg(mean_test_return=("total_return", "mean"),
                mean_sharpe=("sharpe", "mean"),
                mean_max_dd=("max_drawdown", "mean"),
                numerology=("numerology", "first"),
                n_symbols=("symbol", "nunique"))
           .sort_values("mean_test_return", ascending=False)
           .round(4))
    return agg.head(n)


def main() -> None:
    outfits_cfg = load_outfits("config/outfits.yaml")
    universe = load_universe("config/universe.yaml")
    md = MarketData("kraken", cache_dir="data/cache")

    print("Running empirical outfit study (real Kraken data, fees + slippage modeled)...")
    df = run_study(outfits_cfg, universe, md)

    RESULTS_DIR.mkdir(exist_ok=True)
    df.to_csv(RESULTS_DIR / "outfit_study_raw.csv", index=False)

    summary = summarize(df)
    summary.to_csv(RESULTS_DIR / "outfit_study_summary.csv", index=False)

    # Coverage: how many of the 25 named outfits had enough history to form?
    n_named = df[df["kind"] == "outfit"]["name"].nunique()
    cov = (df[(df["kind"] == "outfit") & (df["window"] == "full")]
           .groupby("timeframe")["formed"]
           .apply(lambda s: f"{int(s.groupby(df['name']).any().sum())}/{n_named}"))
    print("\nOutfit history coverage (formed / total) — Kraken serves ~720 bars max:")
    for tf, frac in cov.items():
        print(f"  {tf}: {frac} outfits could fully form")

    pd.set_option("display.width", 160)
    print("\n================ HEADLINE: outfits vs buy-and-hold vs random ================")
    print(summary.to_string(index=False))
    print("\nReading this: 'beat_rate' = fraction of (outfit, symbol) pairs whose")
    print("held-out test return beat that symbol's buy-and-hold. If outfit_beat_rate")
    print("is not clearly above random_beat_rate, the named/numerology outfits are")
    print("no better than random period sets — i.e. no special edge.\n")

    for tf in summary["timeframe"]:
        print(f"---- Top outfits on {tf} (held-out test window) ----")
        print(top_outfits(df, tf).to_string())
        print()

    print(f"Raw rows: {len(df)}  ->  {RESULTS_DIR}/outfit_study_raw.csv")


if __name__ == "__main__":
    main()
