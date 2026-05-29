# SMA-outfits Architecture

## 1. Guiding principles

1. **One interface, three executions.** The *same* `Strategy` and `RiskManager`
   code runs against a `BacktestBroker`, a `PaperBroker`, and a `LiveBroker`
   (Kraken now, Webull later). The only thing that changes between
   `mode: backtest | paper | live` is **which `Broker` (and which `DataFeed`) is
   constructed**. Nothing downstream of the broker boundary knows or cares which
   one it got.
2. **Live is off by default, fail-closed.** A real order can leave the process
   **only** if `mode == live` AND `config.live.confirm == true` AND the
   `RiskManager` mode-gate passes. Every other path either routes to the paper
   simulator or calls Kraken `AddOrder(validate=true)` (no order placed). This is
   enforced in *two* independent places (the broker factory and the risk gate),
   so a single bug cannot place a live order.
3. **Risk sits between strategy and broker — always.** Strategies emit
   *intents* (`Signal`s), never orders. The `RiskManager.check_order()` gate is
   the only thing that converts an approved, sized intent into an `Order`, and
   `OrderRouter` is physically the only component that calls `broker.place_order`.
4. **Vectors for research, events for trading.** `vectorbt` sweeps the whole
   ~25-outfit grid in one vectorized pass (research). The live/paper path is a
   simple bar-by-bar event loop reusing the *identical* signal math from
   `indicators.py` + `strategy/`. The two share the indicator core so a backtest
   edge and a live signal can never silently diverge.
5. **Config is the source of truth.** The four existing YAML files
   (`config.yaml`, `outfits.yaml`, `universe.yaml`, `risk.yaml`) are loaded and
   pydantic-validated at startup; nothing is hardcoded that a config already
   expresses.

## 2. Components and responsibilities

| Component | Module(s) | Responsibility |
|---|---|---|
| **Config** | `config/` (loader, models) | Parse + pydantic-validate the 4 YAML files. Fail-closed. Build the live-mode interlock value. |
| **Indicators** | `indicators.py` (exists) | Pure functions: `sma`, `multi_sma`, `regime`, `crossover`, `key_level_breach`. No I/O, no lookahead. |
| **Data layer** | `data/` | `DataFeed` protocol + adapters. Normalize any source to a tz-aware UTC OHLCV DataFrame. On-disk parquet cache. REST backfill (ccxt / Kraken Trades rebuild) + live WS feed. |
| **Strategy engine** | `strategy/` | Turn outfits/systems + an OHLCV frame into `Signal`s. `CrossoverStrategy` (fast/slow) and `OutfitStrategy` (arbitrary multi-MA + systems trend rules). Identical math in backtest and live. |
| **Backtest / sweep** | `backtest/` | vectorbt-based grid runner over outfits x symbols x timeframes; metrics vs buy-and-hold; walk-forward splitter; optional backtesting.py cross-check; report writer. |
| **Risk layer** | `risk/` | `RiskManager.check_order()` gate: kill-switch (persisted, crash-proof), sizing, stops, portfolio caps, per-order guards, rate self-throttle, idempotency, live interlocks. |
| **Broker abstraction** | `broker/` | `Broker` protocol + `BacktestBroker`, `PaperBroker`, `KrakenBroker` (now), `WebullBroker` (later). Symbol mapping + exchange precision live *inside* each adapter. |
| **Order routing** | `engine/router.py` | The *only* caller of `broker.place_order`. Runs every order through `RiskManager` first; attaches idempotency keys; reconciles fills. |
| **Engine loop** | `engine/` | Wires DataFeed -> Strategy -> RiskManager -> OrderRouter -> Broker. `BacktestEngine` (vectorized) and `LiveEngine` (event loop, used for both paper and live). |
| **Portfolio/state** | `portfolio/` | Live ledger: cash, positions, equity (free quote + MTM), realized/unrealized PnL, fill history. Feeds equity to the RiskManager. |
| **CLI / app** | `cli.py`, `app.py` | typer entrypoints: `backtest`, `sweep`, `paper`, `live`, `killswitch`. `app.py` builds the right objects per mode. |
| **Observability** | `audit.py`, `logging.py` | structlog JSON audit trail of every risk decision + order lifecycle. |

## 3. The Broker abstraction (the heart of backtest/paper/live sharing)

```
              constructs ONE of these per `mode`
              (broker.factory.make_broker(config))
                            |
        +-------------------+--------------------+--------------------+
        |                   |                    |                    |
  BacktestBroker       PaperBroker          KrakenBroker         WebullBroker
  (historical CSV)   (LIVE feed +         (python-kraken-sdk,   (webull-openapi,
   instant fills,     simulated cash/      REAL orders, gated    LATER)
   modeled fees/slip) positions ledger,    by validate/confirm)
                       real fees/min)
        \___________________ all implement the SAME Broker protocol ___________________/
                            place_order / cancel_order / fetch_ohlcv /
                            get_balances / get_positions / stream / map_symbol
```

Because all four satisfy one `Broker` protocol, the `Strategy`, `RiskManager`,
`OrderRouter`, and `Portfolio` are written **once** and are broker-agnostic. The
promotion path is purely a config edit:

- `mode: backtest` -> `BacktestBroker` + `CachedDataFeed` (historical parquet).
- `mode: paper` -> `PaperBroker` wrapping the **real** `KrakenDataFeed` WS feed,
  so prices are live but money is fake; fills modeled at next candle / book with
  Kraken's real maker/taker fees and per-pair `ordermin`/`costmin`.
- `mode: live` -> `KrakenBroker` (real orders) — **only** if `live.confirm: true`.

`PaperBroker` exists because **Kraken spot has no public sandbox** (only Futures
has demo). `validate=true` is used as an extra pre-flight (it checks an order is
well-formed and places nothing) but does not simulate fills, so we own the paper
layer. For live safety the `KrakenBroker.place_order` first sends the exact order
with `validate=true` and asserts a clean response before sending the real one
(`live_safety.dry_run_validate_first`).

## 4. Data flow

### Backtest / sweep (vectorized, research)
```
config.yaml + outfits.yaml + universe.yaml
        |
   ConfigLoader (pydantic validate, fail-closed)
        |
   CachedDataFeed.fetch_ohlcv()  --(parquet cache; ccxt/Kraken backfill on miss)-->  OHLCV DataFrame(s)
        |                                                                              (UTC, label='right')
   align to common DatetimeIndex, slice to longest-MA warm-up window
        |
   SweepRunner (vectorbt):  MA.run(windows=[all outfit periods]) -> crossed_above/below
        |                   entries/exits .fshift(1)  (NO same-bar lookahead)
        |                   Portfolio.from_signals(fees=fee_pct, slippage=slippage_pct, freq=tf)
        |
   metrics table (Sharpe, CAGR, MaxDD, return) per (outfit x symbol x timeframe)
        |                   + buy-and-hold baseline column
        |                   + vbt.Splitter walk-forward -> report ONLY OOS
        |
   ReportWriter -> backtest_results/ (rich tables + matplotlib plots)
        |
   [optional] top 2-3 finalists -> backtesting.py event-driven cross-check
```
The `BacktestBroker` exists so the *same event-loop engine* can also replay
history bar-by-bar for an apples-to-apples reconciliation against vectorbt, and
to validate the risk layer on historical data before any network call.

### Paper / live (event-driven, identical code, broker swapped)
```
LiveEngine.run() loop, per closed bar (from KrakenDataFeed WS 'ohlc' / 'ticker'):
   1. DataFeed yields a finalized candle  ----------------> append to rolling OHLCV frame
   2. Strategy.generate_signals(frame)    ----------------> Signal(symbol, action, ref_price, ...)
   3. Portfolio.snapshot_equity()         ----------------> fresh equity (free quote + MTM marks)
   4. RiskManager.check_order(intent, portfolio, market) -> Decision(ALLOW|REJECT|DELAY, sized Order, stop)
         - kill-switch (persisted) FIRST; mode/confirm gate; sizing; stops;
           portfolio caps; per-order guards; rate self-throttle; idempotency
   5. OrderRouter.submit(order)  (ONLY caller of broker.place_order)
         - if not (mode==live AND confirm): PaperBroker fill  OR  Kraken validate=true
         - else: KrakenBroker real order (validate pre-flight -> real AddOrder)
   6. Broker emits Fill (WS 'executions' for Kraken live; simulated for paper)
   7. Portfolio.apply_fill(fill); RiskManager.on_fill(fill) -> update persisted
         equity peak / day PnL / consecutive-loss counter / idempotency ledger (atomic)
   8. audit.log(decision, order, fill)
```

## 5. ASCII component diagram

```
                         +-------------------------------+
                         |          config/ (YAML)        |
                         |  config.yaml  outfits.yaml     |
                         |  universe.yaml  risk.yaml      |
                         +---------------+---------------+
                                         | load + pydantic validate (fail-closed)
                                         v
+------------------+        +-------------------------------+        +------------------+
|   DataFeed       |        |            ENGINE             |        |   indicators.py  |
| (protocol)       |        |  BacktestEngine | LiveEngine  |        | sma/multi_sma/   |
|  CachedFeed      +------->|                               |<-------+ regime/crossover/|
|  KrakenWSFeed    | OHLCV  |  per-bar / vectorized orchestr |signals | key_level_breach |
|  WebullFeed(L8R) |        +----+-------------------+------+        +--------+---------+
+------------------+             |                   ^                        |
                                 v                   |                        v
                        +-----------------+          |               +------------------+
                        |   STRATEGY      |          |               |    BACKTEST      |
                        | Crossover /     |--Signal--+               | vectorbt sweep   |
                        | Outfit / Systems|                          | walk-forward     |
                        +-----------------+                          | report vs B&H    |
                                 |                                   | (bt.py x-check)  |
                                 v                                   +------------------+
                        +-----------------------+
                        |     RISK MANAGER       |   <--- risk.yaml
                        | check_order():         |
                        |  kill-switch (disk) -> |---> Decision (ALLOW/REJECT/DELAY,
                        |  mode/confirm gate ->  |          sized Order + stop)
                        |  sizing -> stops ->    |
                        |  portfolio caps ->     |   <--- Portfolio (equity, positions)
                        |  guards -> throttle -> |
                        |  idempotency           |
                        +-----------+-----------+
                                    | ALLOW only
                                    v
                        +-----------------------+        the ONLY caller of place_order
                        |     ORDER ROUTER       |
                        +-----------+-----------+
                                    |
              +---------------------+----------------------+
              |  Broker (protocol): place/cancel/fetch/    |
              |  balances/positions/stream/map_symbol      |
              +----+----------+-----------+-----------+-----+
                   |          |           |           |
            BacktestBroker PaperBroker KrakenBroker WebullBroker
             (history)    (live feed,  (python-     (webull-
                          fake money)  kraken-sdk)  openapi, L8R)
                   |          |           |           |
                   +----------+-----+-----+-----------+
                                    | Fill
                                    v
                        +-----------------------+      +------------------+
                        |      PORTFOLIO         |----->|  audit/log       |
                        | cash/positions/equity  |      | structlog JSON   |
                        | realized+unreal PnL    |      | every decision   |
                        +-----------------------+      +------------------+
```

## 6. Mode interlock (why a live order is hard)

```
place a REAL order  <==  ALL of:
    config.mode == "live"
AND config.live.confirm == true
AND risk.live_safety.require_confirm_flag == true
AND RiskManager mode-gate passes (kill-switch not halted, all caps ok)
AND KrakenBroker validate=true pre-flight returned no error
```
If any link is false: `BacktestEngine` uses `BacktestBroker`; `paper` uses
`PaperBroker`; a misconfigured `live` falls back to Kraken `validate=true` (no
order). The broker factory refuses to construct `KrakenBroker` in real-order
mode without the confirm flag, and `RiskManager` independently refuses to emit a
non-validate order — two gates, no single point of failure.

## 7. Symbol & precision handling

`universe.yaml` uses ccxt `BASE/QUOTE` (`BTC/USD`). That string is the canonical
in-app symbol. Each broker adapter owns its own mapping:
- `KrakenBroker`: `BTC/USD` -> REST `XBTUSD` / WS v2 `BTC/USD`, and reads the
  OHLC response's single non-`last` key (often `XXBTZUSD`). At startup it fetches
  `AssetPairs` for `ordermin`, `costmin`, `pair_decimals`, `lot_decimals` and
  caches them; volume/price are formatted as **strings** via `Decimal.quantize`.
- `WebullBroker` (later): `BTC/USD` is N/A; equities map straight to ticker, with
  daily-vs-minute adjustment normalization in the data layer.

The strategy and risk layers never see exchange symbol forms — only `BTC/USD`.

---

# Interfaces

```python
# src/smaoutfits/types.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class Mode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Action(str, Enum):
    BUY = "buy"          # open / add long
    SELL = "sell"        # close / reduce long
    HOLD = "hold"        # no change (regime unchanged)
    FLAT = "flat"        # exit to zero


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    STOP_LOSS_LIMIT = "stop_loss_limit"


class OrderStatus(str, Enum):
    PENDING = "pending"      # created locally, not yet sent
    VALIDATED = "validated"  # passed Kraken validate=true (no real order yet)
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


class Timeframe(str, Enum):
    M1 = "1m"; M5 = "5m"; M15 = "15m"; M20 = "20m"; M30 = "30m"
    H1 = "1h"; H4 = "4h"; D1 = "1d"; W1 = "1w"


@dataclass(frozen=True)
class Signal:
    """A strategy INTENT. Strategies emit these; never orders."""
    symbol: str                 # canonical BASE/QUOTE, e.g. "BTC/USD"
    action: Action
    timestamp: datetime         # bar close time (UTC) the signal is knowable at
    ref_price: Decimal          # reference/last close used to derive the signal
    strength: float = 1.0       # optional conviction in [0,1]
    outfit_id: Optional[str] = None
    timeframe: Optional[Timeframe] = None
    meta: dict = field(default_factory=dict)   # e.g. {"fast":10,"slow":50,"numerology":False}


@dataclass
class Order:
    """A risk-approved, sized, exchange-precision order ready for a broker."""
    symbol: str                 # canonical BASE/QUOTE
    side: Side
    order_type: OrderType
    volume: Decimal             # base-asset qty, snapped to lot_decimals (string-safe)
    price: Optional[Decimal] = None        # required for LIMIT; None for MARKET
    stop_price: Optional[Decimal] = None   # protective stop (mandatory on entries)
    take_profit: Optional[Decimal] = None
    time_in_force: str = "GTC"
    post_only: bool = True
    reduce_only: bool = False
    cl_ord_id: str = ""         # idempotency key (Kraken userref / cl_ord_id; Webull client_order_id)
    validate_only: bool = False # True => Kraken validate=true (no real order placed)
    status: OrderStatus = OrderStatus.PENDING
    meta: dict = field(default_factory=dict)


@dataclass
class Fill:
    """A (partial or full) execution reported by a broker."""
    cl_ord_id: str
    symbol: str
    side: Side
    volume: Decimal             # base qty filled
    price: Decimal              # fill price
    fee: Decimal                # quote-currency fee actually charged
    timestamp: datetime         # UTC
    venue_order_id: Optional[str] = None
    is_final: bool = True       # True when the order is fully done
    meta: dict = field(default_factory=dict)


@dataclass
class Position:
    symbol: str                 # canonical BASE/QUOTE
    volume: Decimal             # signed base qty (long > 0; spot stays >= 0)
    avg_entry_price: Decimal
    stop_price: Optional[Decimal] = None
    opened_at: Optional[datetime] = None
    realized_pnl: Decimal = Decimal("0")

    def notional(self, mark: Decimal) -> Decimal:
        return abs(self.volume) * mark

    def unrealized_pnl(self, mark: Decimal) -> Decimal:
        return (mark - self.avg_entry_price) * self.volume


class RiskAction(str, Enum):
    ALLOW = "allow"
    REJECT = "reject"
    DELAY = "delay"     # rate-throttled; resubmit after retry_after seconds


@dataclass
class Decision:
    """Result of RiskManager.check_order()."""
    action: RiskAction
    order: Optional[Order] = None        # the sized, precision-snapped order if ALLOW
    reason: str = ""                     # rule that fired (audited)
    retry_after_s: float = 0.0           # set when action == DELAY
    numbers: dict = field(default_factory=dict)  # the figures behind the decision (audited)
```

```python
# src/smaoutfits/broker/base.py
from __future__ import annotations
from typing import Protocol, Iterator, runtime_checkable
from decimal import Decimal
import pandas as pd
from smaoutfits.types import Order, Fill, Position


@dataclass  # (in a small module-local dataclass file)
class PairMeta:
    symbol: str
    ordermin: Decimal     # min base-asset order size
    costmin: Decimal      # min quote notional
    price_decimals: int   # pair_decimals
    lot_decimals: int     # volume precision
    tick_size: Decimal


@runtime_checkable
class Broker(Protocol):
    """One interface for backtest, paper, Kraken-live, and Webull-live.

    Strategy / RiskManager / OrderRouter / Portfolio depend ONLY on this.
    """
    name: str
    supports_live: bool          # False for Backtest/Paper; True only for real venues

    def map_symbol(self, canonical: str) -> str: ...
        # "BTC/USD" -> venue form ("XBTUSD" REST / "BTC/USD" WS / "AAPL" equity)

    def pair_meta(self, symbol: str) -> PairMeta: ...
        # AssetPairs-derived precision/min, cached at startup

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int = 720
    ) -> pd.DataFrame: ...
        # canonical UTC OHLCV frame (open/high/low/close/volume), label='right'

    def get_balances(self) -> dict[str, Decimal]: ...
        # asset -> free balance (quote currency drives equity)

    def get_positions(self) -> list[Position]: ...

    def place_order(self, order: Order) -> Order: ...
        # returns the order with venue_order_id/status; if order.validate_only,
        # performs a validate-only check and places NOTHING (returns VALIDATED).

    def cancel_order(self, cl_ord_id: str) -> bool: ...

    def stream(self, symbols: list[str], timeframe: str) -> Iterator[pd.Series]: ...
        # yields FINALIZED candles (live: Kraken WS 'ohlc'; backtest: replayed bars)

    def stream_fills(self) -> Iterator[Fill]: ...
        # live: Kraken WS 'executions' / Webull gRPC; paper/backtest: simulated
```

```python
# src/smaoutfits/data/base.py
from typing import Protocol, Iterator
import pandas as pd


class DataFeed(Protocol):
    """Source of OHLCV. Normalizes everything to the canonical frame.

    Canonical frame: tz-aware UTC DatetimeIndex (bar CLOSE time, label='right'),
    float columns ['open','high','low','close','volume']. One symbol per frame.
    """
    def history(self, symbol: str, timeframe: str, lookback_bars: int) -> pd.DataFrame: ...
        # backfill + cache; used to seed MAs and to backtest
    def latest(self, symbol: str, timeframe: str) -> pd.Series: ...
        # most recent FINALIZED candle
    def subscribe(self, symbols: list[str], timeframe: str) -> Iterator[pd.Series]: ...
        # live stream of finalized candles (one Series per closed bar)
```

```python
# src/smaoutfits/strategy/base.py
from abc import ABC, abstractmethod
import pandas as pd
from smaoutfits.types import Signal


class Strategy(ABC):
    """Pure signal generator. No I/O, no broker, no sizing. Deterministic:
    same frame in -> same signals out (so backtest and live cannot diverge)."""
    symbol: str
    timeframe: str

    @property
    @abstractmethod
    def warmup_bars(self) -> int: ...
        # = max MA period; metrics/signals undefined before this

    @abstractmethod
    def generate_signals(self, frame: pd.DataFrame) -> list[Signal]: ...
        # vectorized over the whole frame (backtest) or called on the rolling
        # window each bar (live). MUST lag one bar to avoid same-bar lookahead.
```

```python
# src/smaoutfits/risk/manager.py
from typing import Protocol
from smaoutfits.types import Signal, Decision
from smaoutfits.portfolio.ledger import Portfolio


class MarketContext(Protocol):
    """Fresh market facts the risk layer needs (never stale)."""
    def last_price(self, symbol: str) -> "Decimal": ...
    def atr(self, symbol: str, period: int) -> "Decimal": ...
    def pair_meta(self, symbol: str) -> "PairMeta": ...
    def is_stale(self, symbol: str) -> bool: ...


class RiskManager(ABC):
    """The single gate between strategy signals and the broker.

    check_order() is the ONLY way an Order comes into existence. Order applies
    the rules in this fixed order (fail-closed throughout):
      1. kill-switch (persisted, read first)         -> REJECT if halted
      2. mode/confirm gate                            -> force validate_only unless live+confirm
      3. stale-data / price-sanity                    -> REJECT
      4. require_stop_on_entry                         -> REJECT naked entry
      5. position sizing (clamped)                     -> SKIP if < min_position_quote
      6. portfolio caps (gross/positions/corr/reserve) -> REJECT
      7. per-order guards (slippage/min/precision/notional) -> REJECT or round
      8. rate self-throttle                            -> DELAY (not drop)
      9. idempotency                                   -> REJECT resend of used key
    """
    @abstractmethod
    def check_order(self, intent: Signal, portfolio: Portfolio,
                    market: MarketContext) -> Decision: ...

    @abstractmethod
    def on_fill(self, fill: "Fill", portfolio: Portfolio) -> None: ...
        # atomically update persisted equity peak, day PnL, consecutive-loss
        # counter, idempotency ledger after every fill/close.

    @abstractmethod
    def is_halted(self) -> bool: ...
```

```python
# src/smaoutfits/engine/base.py
from abc import ABC, abstractmethod


class Engine(ABC):
    """Wires DataFeed -> Strategy -> RiskManager -> OrderRouter -> Broker ->
    Portfolio. BacktestEngine (vectorized) and LiveEngine (event loop, used for
    BOTH paper and live) subclass this. The mode only changes which Broker and
    DataFeed were injected by app.make_engine(config)."""
    @abstractmethod
    def run(self) -> "RunResult": ...


# src/smaoutfits/engine/live_engine.py  (the shared paper/live loop)
class LiveEngine(Engine):
    def __init__(self, feed, strategies, risk, router, portfolio, market, audit): ...

    def run(self):
        for candle in self.feed.subscribe(self.symbols, self.timeframe):
            self._on_bar(candle)            # one place; identical for paper & live

    def _on_bar(self, candle):
        self.portfolio.mark(candle)                          # 1. update MTM/equity
        for strat in self.strategies:
            for signal in strat.generate_signals(self._window(strat.symbol)):  # 2.
                decision = self.risk.check_order(            # 3. THE gate
                    signal, self.portfolio, self.market)
                self.audit.decision(decision)
                if decision.action is RiskAction.ALLOW:
                    self.router.submit(decision.order)       # 4. ONLY place_order caller
                elif decision.action is RiskAction.DELAY:
                    self.router.defer(decision.order, decision.retry_after_s)
        for fill in self.router.poll_fills():                # 5. reconcile fills
            self.portfolio.apply_fill(fill)
            self.risk.on_fill(fill, self.portfolio)          # 6. persist state
            self.audit.fill(fill)


# src/smaoutfits/engine/router.py
class OrderRouter:
    """The ONLY component that calls broker.place_order (defense in depth:
    even if a Decision wrongly ALLOWs a live order, the broker factory already
    refused to build a real-order broker without live.confirm)."""
    def __init__(self, broker: "Broker", risk: "RiskManager", audit): ...
    def submit(self, order: Order) -> Order: ...    # idempotency check -> broker.place_order
    def defer(self, order: Order, after_s: float) -> None: ...
    def poll_fills(self) -> list[Fill]: ...
```
