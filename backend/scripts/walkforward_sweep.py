"""Walk-forward parameter sweep (research only — NOT trading advice).

Evaluates strategy parameter variations on REAL Binance BTC/USDT klines
(``backend/data/``, fetched from data.binance.vision) using the existing
``BacktestEngine`` + walk-forward tooling. For every fixed parameter set we run
a walk-forward with a single-parameter grid, so each fold's ``test`` report is
an honest out-of-sample window result; the per-window consistency is the share
of OOS windows with positive return. The target the owner set is >=70% of
windows positive — with 6 folds that means 5/6.

SPOT 1x LONG-ONLY, realistic fees (taker 10 bps), mandatory stop-loss — the
four-gate pipeline runs inside every backtest. This tool recommends nothing and
never executes; results are research only and no strategy here is a profit
guarantee. Nothing in this script can enable live trading or change a gate.

Method notes (kept identical across every row so rows are comparable):
* 6 folds per timeframe — strict. 4-fold runs can show 75-100% from one lucky
  window; the sweep headline has always used 6 and it stays 6.
* train 60% / validation 20% / test 20% inside each fold; the reported number
  is always the fold's OOS ``test`` segment.
* ``warmup_bars=WARMUP`` real bars immediately *before* each segment are
  prepended so slow indicators (EMA 50, Donchian 15, regime detection) have
  history. Those bars are past data at segment time (no look-ahead) and the
  engine skips them entirely, so no trade or equity from them is counted.
* Same ``BacktestConfig`` (5000 USDT start, 500 USDT per trade) everywhere.

Usage:  cd backend && .venv/bin/python scripts/walkforward_sweep.py
Output: data/results/walkforward_results.json + data/results/meta.json + table.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Run as a plain script (`python scripts/walkforward_sweep.py`) from anywhere:
# make sure the backend package root is importable.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from halaltrade.backtest import BacktestConfig, BacktestEngine  # noqa: E402
from halaltrade.config import Settings  # noqa: E402
from halaltrade.research.dataset import load_candles  # noqa: E402
from halaltrade.research.walkforward import (  # noqa: E402
    WalkForwardConfig,
    walk_forward,
)
from halaltrade.strategies import (  # noqa: E402
    SmaCrossStrategy,
    EmaTrendStrategy,
    RsiMeanReversionStrategy,
    DonchianBreakoutStrategy,
)

DATA = Path(__file__).resolve().parents[1] / "data"
OUT = DATA / "results"

# 6 folds everywhere = tighter, more honest consistency test.
FOLDS = {"1h": 6, "4h": 6, "1d": 6}
MIN_BARS = 10
# 60 real prior bars of warm-up per segment: enough for EMA(50) and for the
# regime classifier (needs >= 30 bars). Skipped by the engine, never scored.
WARMUP = 60

FAMILIES: dict[str, type] = {
    "sma": SmaCrossStrategy,
    "ema": EmaTrendStrategy,
    "rsi": RsiMeanReversionStrategy,
    "donchian": DonchianBreakoutStrategy,
}


def make_settings() -> Settings:
    return Settings(
        trading_mode="backtest", live_enabled=False,
        max_position_size=5000.0, max_exposure=5000.0,
        max_loss_per_trade=500.0, max_daily_loss=1000.0,
        max_drawdown=0.5, min_account_balance=100.0,
        min_notional=5.0, max_data_age_seconds=3600 * 1000.0,
    )


def make_bt_config(timeframe: str) -> BacktestConfig:
    return BacktestConfig(
        starting_equity=5000.0, trade_amount=500.0,
        stop_loss_pct=0.02, take_profit_pct=0.05,
        timeframe=timeframe, symbol="BTCUSDT",
    )


@dataclass
class SweepRow:
    family: str
    timeframe: str
    params: dict[str, Any]
    folds_ok: int
    oos_returns: list[float]
    consistency_pct: float        # share of OOS windows with return > 0
    mean_oos_return: float        # mean OOS return across folds
    mean_is_return: float
    max_drawdown: float           # worst OOS max drawdown across folds
    trade_count: int              # total OOS trades
    # Whole-history single backtest of the same fixed params, no OOS split:
    # the "would this have worked end to end?" honesty check.
    full_return: float = 0.0
    full_max_drawdown: float = 0.0
    full_trade_count: int = 0
    best_params: dict | None = None

    def fmt(self) -> str:
        return f"{self.consistency_pct:6.1f}%"


def _single(grid: dict) -> dict:
    """A single-element grid => the walk-forward has exactly one combo, so the
    per-fold ``test`` report is the honest OOS result for these fixed params."""
    return {k: [v] for k, v in grid.items()}


def _run(family: str, candles, params: dict, timeframe: str) -> SweepRow:
    cls = FAMILIES[family]
    grid = params | {"timeframe": timeframe}
    wf = walk_forward(
        cls, candles, _single(grid),
        config=WalkForwardConfig(n_folds=FOLDS[timeframe],
                                 min_segment_bars=MIN_BARS,
                                 top_k=1, metric="total_return",
                                 warmup_bars=WARMUP),
        backtest_config=make_bt_config(timeframe),
        settings=make_settings(),
    )
    oos = [f.test.total_return for f in wf.folds if f.test is not None]
    dd = [f.test.max_drawdown for f in wf.folds if f.test is not None]
    trades = sum(f.test.trade_count for f in wf.folds if f.test is not None)
    isr = [f.train.total_return for f in wf.folds if f.train is not None]
    full = _full_period(family, candles, params, timeframe)
    return SweepRow(
        family=family, timeframe=timeframe, params=params,
        folds_ok=len(oos), oos_returns=[round(x, 5) for x in oos],
        consistency_pct=100.0 * sum(1 for x in oos if x > 0) / len(oos) if oos else 0.0,
        mean_oos_return=sum(oos) / len(oos) if oos else 0.0,
        mean_is_return=sum(isr) / len(isr) if isr else 0.0,
        max_drawdown=max(dd) if dd else 0.0,
        trade_count=trades,
        full_return=full[0], full_max_drawdown=full[1], full_trade_count=full[2],
    )


def _full_period(family: str, candles, params: dict, timeframe: str) -> tuple[float, float, int]:
    """One backtest over the WHOLE dataset with the same fixed params.

    No train/test split, no fold — this is the "does it work end to end?"
    check that walk-forward windows alone cannot answer. Same engine, same
    gates, same fees, same 1x spot config.
    """
    strategy = FAMILIES[family](**(params | {"timeframe": timeframe}))
    res = BacktestEngine(make_settings()).run(
        strategy, candles, make_bt_config(timeframe),
        strategy_name=f"full:{family}@{params}")
    return (res.report.total_return, res.report.max_drawdown,
            res.report.trade_count)


def build_sweep(datasets: dict[str, list]) -> list[SweepRow]:
    rows: list[SweepRow] = []

    # ---- RSI mean-reversion on 1d (the family the data favours so far) ------
    # periods 5/7/9 x thresholds (25/75, 30/70, 35/65) with the library's
    # default 0.02/0.04 stop/target ...
    for period in (5, 7, 9):
        for oversold, overbought in ((25, 75), (30, 70), (35, 65)):
            rows.append(_run("rsi", datasets["1d"],
                             {"period": period, "oversold": oversold,
                              "overbought": overbought}, "1d"))
    # ... plus the wider 0.03/0.06 stop/target treatment on periods 5/7/9.
    for period in (5, 7, 9):
        rows.append(_run("rsi", datasets["1d"],
                         {"period": period, "oversold": 30, "overbought": 70,
                          "stop_loss_pct": 0.03, "take_profit_pct": 0.06}, "1d"))
    # Wider stops/targets on the other two threshold pairs for period 7.
    for oversold, overbought in ((25, 75), (35, 65)):
        rows.append(_run("rsi", datasets["1d"],
                         {"period": 7, "oversold": oversold,
                          "overbought": overbought,
                          "stop_loss_pct": 0.03, "take_profit_pct": 0.06}, "1d"))
    # Same candidates on 4h for a timeframe cross-check.
    for period in (7, 14):
        rows.append(_run("rsi", datasets["4h"],
                         {"period": period, "oversold": 30, "overbought": 70,
                          "stop_loss_pct": 0.03, "take_profit_pct": 0.06}, "4h"))

    # ---- Donchian breakout 1d: channels 8/10/12/15 x volume filter ----------
    for channel in (8, 10, 12, 15):
        rows.append(_run("donchian", datasets["1d"], {"channel": channel}, "1d"))
        for vbars in (10, 20):
            rows.append(_run("donchian", datasets["1d"],
                             {"channel": channel, "volume_filter_bars": vbars},
                             "1d"))
    # 4h reference points (channels 10/20) to show the timeframe contrast.
    for channel in (10, 20):
        rows.append(_run("donchian", datasets["4h"], {"channel": channel}, "4h"))

    # ---- EMA trend: pairs x timeframe x {none, volume 10/20, regime-up} -----
    for tf in ("1d", "4h"):
        for fast, slow in ((5, 20), (10, 30), (20, 50)):
            rows.append(_run("ema", datasets[tf],
                             {"fast": fast, "slow": slow}, tf))
            for vbars in (10, 20):
                rows.append(_run("ema", datasets[tf],
                                 {"fast": fast, "slow": slow,
                                  "volume_filter_bars": vbars}, tf))
            rows.append(_run("ema", datasets[tf],
                             {"fast": fast, "slow": slow,
                              "regime_filter": "trending-up"}, tf))

    # ---- SMA baselines (previous delivery's comparison row set) ------------
    for tf in ("4h", "1d"):
        for fast, slow in ((5, 20), (10, 30), (20, 50), (50, 200)):
            rows.append(_run("sma", datasets[tf],
                             {"fast": fast, "slow": slow}, tf))
    rows.append(_run("sma", datasets["1h"], {"fast": 5, "slow": 20}, "1h"))

    # ---- Donchian 4h reference from the first sweep ------------------------
    rows.append(_run("donchian", datasets["4h"], {"channel": 55}, "4h"))
    rows.append(_run("rsi", datasets["1h"],
                     {"period": 14, "oversold": 30, "overbought": 70}, "1h"))
    return rows


def print_table(rows: list[SweepRow]) -> None:
    header = (f"{'family':<9}{'tf':<4}{'params':<46}{'folds':<6}{'cons%':<8}"
              f"{'meanOOS':<10}{'meanIS':<10}{'maxDD':<8}{'trades':<7}"
              f"{'fullRet':<10}{'fullDD':<8}{'fullTr':<7}")
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda r: (-r.consistency_pct, -r.mean_oos_return)):
        p = ",".join(f"{k}={v}" for k, v in sorted(r.params.items()))
        print(f"{r.family:<9}{r.timeframe:<4}{p:<46}{r.folds_ok:<6}"
              f"{r.fmt():<8}{r.mean_oos_return:>8.2%}  {r.mean_is_return:>8.2%}  "
              f"{r.max_drawdown:>6.2%}  {r.trade_count:<7}"
              f"{r.full_return:>8.2%}  {r.full_max_drawdown:>6.2%}  "
              f"{r.full_trade_count:<7}")


def main() -> None:
    datasets = {tf: load_candles(DATA / f"BTCUSDT_{tf}.jsonl")
                for tf in ("1h", "4h", "1d")}
    rows = build_sweep(datasets)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "walkforward_results.json").write_text(
        json.dumps([asdict(r) for r in rows], indent=2))

    ranked = sorted(rows, key=lambda r: (-r.consistency_pct, -r.mean_oos_return))
    winners = [r for r in rows if r.consistency_pct >= 70.0 and r.folds_ok >= 3
               and r.mean_oos_return > 0 and r.trade_count > 0]
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "data.binance.vision (Binance public kline archive) — real data, "
                  "no synthetic substitution",
        "datasets": {tf: {"candles": len(datasets[tf]),
                          "first": str(datasets[tf][0].timestamp.date()),
                          "last": str(datasets[tf][-1].timestamp.date())}
                     for tf in datasets},
        "engine": "BacktestEngine (Spot 1x long-only, taker fee 10bps, "
                  "mandatory stop-loss, four-gate pipeline inside every run)",
        "walkforward": "n_folds=6 per timeframe, train 60% / validation 20% / "
                       "test 20%, single-combo grid so each fold's test report "
                       "is the OOS result for the fixed params (fresh 5000 USDT "
                       "equity per window)",
        "warmup_bars": WARMUP,
        "warmup_note": "real bars before each segment are prepended for "
                       "indicator warm-up; the engine skips them entirely "
                       "(no trades/equity counted) and they are past data at "
                       "segment time, so this is not look-ahead",
        "rows": len(rows),
        "goal": "owner target: positive return in >=70% of OOS windows "
                "(5/6 at six folds)",
        "met_70pct": bool(winners),
        "top10": [
            {"family": r.family, "timeframe": r.timeframe, "params": r.params,
             "consistency_pct": round(r.consistency_pct, 1),
             "mean_oos_return": round(r.mean_oos_return, 5),
             "max_drawdown": round(r.max_drawdown, 5),
             "trade_count": r.trade_count, "folds_ok": r.folds_ok,
             "oos_returns": r.oos_returns,
             "full_period_return": round(r.full_return, 5),
             "full_period_max_drawdown": round(r.full_max_drawdown, 5),
             "full_period_trades": r.full_trade_count}
            for r in ranked[:10]
        ],
        "note": "RESEARCH ONLY — not validated advice, no guarantee. A config "
                "reaching >=70% here is still a research candidate: paper "
                "trading and out-of-sample-forward checks come next.",
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2))

    print("=== data provenance ===")
    for tf, m in meta["datasets"].items():
        print(f"  {tf}: {m['candles']} candles {m['first']} -> {m['last']}")
    print(f"=== {len(rows)} parameter sets, {FOLDS['1d']} folds each, "
          f"warmup={WARMUP} bars, fees 10bps, spot 1x long-only ===\n")
    print_table(rows)

    print("\n=== TOP 10 by consistency, then mean OOS return ===")
    for r in ranked[:10]:
        print(f"  {r.family:<8}{r.timeframe:<4}{str(r.params):<48} "
              f"cons={r.consistency_pct:5.1f}% meanOOS={r.mean_oos_return:+.2%} "
              f"maxDD={r.max_drawdown:.2%} trades={r.trade_count} "
              f"full={r.full_return:+.2%} (trades={r.full_trade_count}) "
              f"per-window={r.oos_returns}")

    print("\n=== Candidates meeting >=70% OOS-window consistency "
          "(positive mean OOS, >=3 folds, >0 trades) ===")
    if not winners:
        print("NONE — no parameter set met the 70% target. Honest best below.")
    for r in sorted(winners, key=lambda r: -r.mean_oos_return):
        print(f"  {r.family} {r.timeframe} {r.params} cons={r.fmt()} "
              f"meanOOS={r.mean_oos_return:.2%} maxDD={r.max_drawdown:.2%} "
              f"trades={r.trade_count} full-period={r.full_return:+.2%} "
              f"per-window={r.oos_returns}")


if __name__ == "__main__":
    main()
