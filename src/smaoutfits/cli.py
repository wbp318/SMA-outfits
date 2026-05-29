"""Command-line interface: `python -m smaoutfits <command>`.

Commands:
  study                         Run the empirical outfit study (real data).
  backtest   --symbol --tf      Vectorized backtest of one strategy vs buy-and-hold.
  paper      --symbol --tf      Replay the engine over recent public data (fake money).
  paper-live --symbol --tf      Forward paper session: step the latest CLOSED bar,
                                persist state, build an equity curve over real time.
  check-kraken                  Read-only + validate-only Kraken connectivity check.

Uses argparse (stdlib) — no extra dependency. Paper trading runs on Kraken's
PUBLIC feed and needs no API keys; it never places a real order.
"""

from __future__ import annotations

import argparse
import os
import time

from . import study as study_mod
from .backtest import buy_and_hold, run_backtest
from .broker import SimulatedBroker, make_broker
from .config import load_app_config, load_outfits, load_risk_config
from .data import MarketData
from .engine import Engine
from .paper_session import PaperSession
from .portfolio import Portfolio
from .risk import KillSwitch, RiskManager
from .strategy import CrossoverStrategy, SystemsStrategy, build_outfit_strategy
from .types import Side


def _load_configs(path: str = "config/config.yaml"):
    if not os.path.exists(path):
        path = "config/config.example.yaml"
    app = load_app_config(path)
    risk = load_risk_config(app.risk_file)
    return app, risk


def _build_strategy(args, outfits):
    """Pick the strategy: explicit --outfit wins, else --system (the README's '3
    systems'), else a plain fast/slow crossover."""
    if getattr(args, "outfit", None):
        return build_outfit_strategy(outfits.by_id(args.outfit))
    system_id = getattr(args, "system", None)
    if system_id:
        system = next((s for s in outfits.systems if s.id == system_id), None)
        if system is None:
            raise SystemExit(f"unknown system {system_id!r}; choices: "
                             f"{[s.id for s in outfits.systems]}")
        return SystemsStrategy(system)
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


def _paper_tick(app, risk, strategy, symbol, timeframe, session_path, md) -> dict:
    """One forward paper step on the latest CLOSED bar. Idempotent per bar.

    Transient network errors return a status dict rather than raising, so a
    long-running --poll session survives a dropped request and tries again.
    """
    try:
        df = md.fetch_ohlcv(symbol, timeframe, max_bars=720)
    except Exception as exc:
        return {"status": "fetch error (will retry)", "error": f"{type(exc).__name__}: {exc}"}
    # Kraken's final candle is the CURRENT (still-forming) interval — drop it so we
    # only ever act on a fully closed bar (no acting on partial data).
    df = df.iloc[:-1]
    if len(df) < strategy.warmup_bars() + 2:
        return {"status": "warming up", "bars": len(df)}

    bar_ts = df.index[-1].isoformat()
    session = PaperSession.load(session_path, symbol=symbol, initial_cash=app.backtest.initial_cash)
    if session.already_processed(bar_ts):
        return {"status": "no new closed bar", "bar": bar_ts}

    portfolio = session.to_portfolio()
    kill = KillSwitch(risk.kill_switch, state_path="data/kill_switch_paper.json")
    engine = Engine(symbol=symbol, strategy=strategy, risk=RiskManager(app, risk, kill),
                    portfolio=portfolio, broker=SimulatedBroker(app.backtest.fee_pct,
                                                                app.backtest.slippage_pct),
                    is_live=False)
    decision = engine.on_bar(df)
    price = float(df["close"].iloc[-1])
    equity = portfolio.equity({symbol: price})
    session.absorb(portfolio, bar_ts, equity)
    session.save(session_path)
    held = portfolio.positions.get(symbol)
    return {
        "status": "stepped", "bar": bar_ts, "price": price, "equity": round(equity, 2),
        "position": round(held.qty, 8) if held else 0.0,
        "decision": (decision.rule if decision else "hold"),
        "halted": kill.halted,
    }


def cmd_paper_live(args) -> None:
    app, risk = _load_configs()
    app.mode = "paper"   # forces the simulated broker; never a real order
    outfits = load_outfits(app.outfits_file)
    strat = _build_strategy(args, outfits)
    md = MarketData(app.exchange.name, app.data.cache_dir)
    safe = args.symbol.replace("/", "-")
    session_path = f"data/paper_{safe}.json"

    if args.reset:
        for f in (session_path, "data/kill_switch_paper.json"):
            try:
                os.remove(f)
            except FileNotFoundError:
                pass
        print(f"reset paper session for {args.symbol}")

    print(f"PAPER-LIVE {strat.name} on {args.symbol} {args.timeframe} "
          f"(fake money; no real orders)")
    iterations = 0
    while True:
        result = _paper_tick(app, risk, strat, args.symbol, args.timeframe, session_path, md)
        print(f"  {result}")
        iterations += 1
        if args.once or (args.iterations and iterations >= args.iterations):
            break
        time.sleep(args.poll)


def cmd_check_kraken(args) -> None:
    """Read-only + validate-only Kraken check. Places NO real order."""
    from .broker_kraken import KrakenBroker
    from .types import Order, OrderType, Side

    app, _ = _load_configs()
    kb = KrakenBroker.from_config(app, allow_live=False)   # cannot place real orders
    print("system status :", kb._market.get_system_status().get("status"))

    bal = kb.get_balances()
    nonzero = {k: v for k, v in bal.items() if v > 0}
    print(f"auth (balances): OK — {len(bal)} assets, {len(nonzero)} funded")

    df = kb.fetch_ohlcv(args.symbol, args.timeframe)
    print(f"OHLCV {args.symbol} {args.timeframe}: {len(df)} bars, "
          f"last close {df['close'].iloc[-1]}")

    meta = kb.pair_meta(args.symbol)
    qty = float(meta.get("ordermin", "0.0001"))
    resp = kb.validate_order(Order(args.symbol, Side.BUY, qty, OrderType.MARKET))
    print(f"validate-only BUY {qty} {args.symbol}: OK (NO order placed)")
    print("  kraken says:", resp.get("descr", resp))
    print("\nAll checks passed. Real orders remain OFF "
          "(allow_live=False; needs mode=live + live.confirm).")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="smaoutfits", description="MA trading framework")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("study", help="run the empirical outfit study").set_defaults(func=cmd_study)

    ck = sub.add_parser("check-kraken", help="read-only + validate-only Kraken connectivity check")
    ck.add_argument("--symbol", default="BTC/USD")
    ck.add_argument("--timeframe", "--tf", default="1h")
    ck.set_defaults(func=cmd_check_kraken)

    for name, func in (("backtest", cmd_backtest), ("paper", cmd_paper)):
        sp = sub.add_parser(name, help=f"{name} a strategy")
        sp.add_argument("--symbol", default="BTC/USD")
        sp.add_argument("--timeframe", "--tf", default="1h")
        sp.add_argument("--bars", type=int, default=720)
        sp.add_argument("--system", default=None,
                        help="README system id (spx_system/ixic_system/dji_system)")
        sp.add_argument("--outfit", default=None,
                        help="outfit id (e.g. sp500); overrides fast/slow")
        sp.add_argument("--fast", type=int, default=20)
        sp.add_argument("--slow", type=int, default=50)
        sp.set_defaults(func=func)

    # Forward paper session — defaults to the README's "10/50/200 System" on 30m.
    pl = sub.add_parser("paper-live", help="forward paper session on the latest closed bar")
    pl.add_argument("--symbol", default="BTC/USD")
    pl.add_argument("--timeframe", "--tf", default="30m")
    pl.add_argument("--system", default="spx_system",
                    help="README system id (spx_system/ixic_system/dji_system)")
    pl.add_argument("--outfit", default=None, help="use an outfit instead of a system")
    pl.add_argument("--fast", type=int, default=20)
    pl.add_argument("--slow", type=int, default=50)
    pl.add_argument("--once", action="store_true", help="run a single tick and exit")
    pl.add_argument("--poll", type=float, default=60.0, help="seconds between ticks when looping")
    pl.add_argument("--iterations", type=int, default=None, help="stop after N ticks")
    pl.add_argument("--reset", action="store_true", help="wipe saved session state first")
    pl.set_defaults(func=cmd_paper_live)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
