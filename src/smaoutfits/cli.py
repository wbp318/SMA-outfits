"""Command-line interface: `python -m smaoutfits <command>`.

Commands:
  study                         Run the empirical outfit study (real data).
  backtest  --symbol --tf       Vectorized backtest of one strategy vs buy-and-hold.
  paper     --symbol --tf       Dry-run the engine over recent public data (fake money).

Uses argparse (stdlib) — no extra dependency. Paper trading runs on Kraken's
PUBLIC feed and needs no API keys; it never places a real order.
"""

from __future__ import annotations

import argparse
import os

from . import study as study_mod
from .backtest import buy_and_hold, run_backtest
from .broker import make_broker
from .config import load_app_config, load_outfits, load_risk_config
from .data import MarketData
from .engine import Engine
from .portfolio import Portfolio
from .risk import KillSwitch, RiskManager
from .strategy import CrossoverStrategy, build_outfit_strategy
from .types import Side


def _load_configs(path: str = "config/config.yaml"):
    if not os.path.exists(path):
        path = "config/config.example.yaml"
    app = load_app_config(path)
    risk = load_risk_config(app.risk_file)
    return app, risk


def _build_strategy(args, outfits):
    if getattr(args, "outfit", None):
        return build_outfit_strategy(outfits.by_id(args.outfit))
    return CrossoverStrategy(args.fast, args.slow)


def cmd_study(_args) -> None:
    study_mod.main()


def cmd_backtest(args) -> None:
    app, _ = _load_configs()
    outfits = load_outfits(app.outfits_file)
    md = MarketData(app.exchange.name, app.data.cache_dir)
    df = md.fetch_ohlcv(args.symbol, args.timeframe, max_bars=args.bars)
    strat = _build_strategy(args, outfits)
    res = run_backtest(strat, df, args.timeframe, fee_pct=app.backtest.fee_pct,
                       slippage_pct=app.backtest.slippage_pct)
    bh = buy_and_hold(df, args.timeframe, fee_pct=app.backtest.fee_pct,
                      slippage_pct=app.backtest.slippage_pct)
    print(f"\n{strat.name} on {args.symbol} {args.timeframe} ({len(df)} bars, formed={res.formed})")
    print(f"  strategy : return {res.total_return:+.2%}  Sharpe {res.sharpe:+.2f}  "
          f"maxDD {res.max_drawdown:.2%}  trades {res.n_trades}")
    print(f"  buy&hold : return {bh.total_return:+.2%}  Sharpe {bh.sharpe:+.2f}  "
          f"maxDD {bh.max_drawdown:.2%}")


def cmd_paper(args) -> None:
    app, risk = _load_configs()
    app.mode = "paper"   # force the simulated broker; never a real order
    outfits = load_outfits(app.outfits_file)
    md = MarketData(app.exchange.name, app.data.cache_dir)
    df = md.fetch_ohlcv(args.symbol, args.timeframe, max_bars=args.bars)

    strat = _build_strategy(args, outfits)
    portfolio = Portfolio(cash=app.backtest.initial_cash)
    kill = KillSwitch(risk.kill_switch, state_path="data/kill_switch_paper.json")
    kill.reset()   # fresh baseline for this dry-run session
    engine = Engine(symbol=args.symbol, strategy=strat, risk=RiskManager(app, risk, kill),
                    portfolio=portfolio, broker=make_broker(app), is_live=False)

    equity = engine.run_replay(df)
    bh = buy_and_hold(df, args.timeframe, fee_pct=app.backtest.fee_pct,
                      slippage_pct=app.backtest.slippage_pct)
    n_orders = sum(1 for _, d in engine.decisions if d.allowed)
    buys = sum(1 for f in portfolio.fills if f.side == Side.BUY)
    sells = sum(1 for f in portfolio.fills if f.side == Side.SELL)
    final = equity.iloc[-1]
    ret = final / app.backtest.initial_cash - 1.0
    print(f"\nPAPER (fake money) {strat.name} on {args.symbol} {args.timeframe} — {len(df)} bars")
    print(f"  start cash : {app.backtest.initial_cash:,.2f}")
    print(f"  final equity: {final:,.2f}  ({ret:+.2%})")
    print(f"  buy&hold    : {bh.total_return:+.2%}  (same window)")
    print(f"  orders filled: {n_orders}  (buys {buys}, sells {sells})")
    print(f"  kill switch halted: {kill.halted}{' — ' + kill.halt_reason if kill.halted else ''}")
    print("  NOTE: dry-run over recent public bars; no real orders, no API keys used.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="smaoutfits", description="MA trading framework")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("study", help="run the empirical outfit study").set_defaults(func=cmd_study)

    for name, func in (("backtest", cmd_backtest), ("paper", cmd_paper)):
        sp = sub.add_parser(name, help=f"{name} a strategy")
        sp.add_argument("--symbol", default="BTC/USD")
        sp.add_argument("--timeframe", "--tf", default="1h")
        sp.add_argument("--bars", type=int, default=720)
        sp.add_argument("--outfit", default=None,
                        help="outfit id (e.g. sp500); overrides fast/slow")
        sp.add_argument("--fast", type=int, default=20)
        sp.add_argument("--slow", type=int, default=50)
        sp.set_defaults(func=func)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
