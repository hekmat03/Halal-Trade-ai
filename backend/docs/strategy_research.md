# Strategy research — walk-forward sweep (Delivery 6)

**RESEARCH ONLY. Nothing in this document is trading advice, a profit
guarantee, or a validated strategy.** Every number here comes from the
backtest engine with the four-gate pipeline (Shariah → Risk → Security →
Execution validation) running inside every simulated run, on real market data.
No gate, limit or mode was changed to produce any result, and live trading
remains disabled by default.

Owner's goal for this research: **a parameter set with a positive return in
≥70% of walk-forward windows.** At 6 folds that means 5/6.

## Data

* Source: `data.binance.vision` — Binance's own public kline archive (the live
  REST API is geo-blocked from this host, HTTP 451). **Real data: no synthetic
  substitution anywhere.** If the archive cannot be fetched the loader raises
  `DataUnavailableError` instead of inventing rows.
* Symbol: BTC/USDT, spot.
* Windows: **1d** 984 candles (2024-01-01 → 2026-09-10), **4h** 1164 candles
  and **1h** 4656 candles (both 2026-03-01 → 2026-09-10).
* Raw klines stay gitignored (`backend/data/`); the sweep output committed here
  is `backend/data/results/walkforward_results.json` plus `meta.json`.

## Methodology

* **Engine**: `BacktestEngine` — Spot, 1x, long-only (BUY opens, SELL only
  disposes of an owned position; never a short). Taker fee **10 bps** per side,
  mandatory stop-loss on every entry, take-profit optional. Fresh 5000 USDT
  equity per window, fixed 500 USDT per trade (manual-size rule: nothing is
  auto-sized).
* **Gates**: every simulated order passes the same four gates as the live
  path (Signal → Shariah → Risk → Security → Execution validation). A rejected
  order simply does not happen in the simulation either.
* **Walk-forward**: 6 folds per timeframe — strict and unchanged. Each fold is
  split train 60% / validation 20% / test 20%; because each row is a
  single-combination grid, params are not re-tuned per fold and the fold's
  `test` segment is the honest out-of-sample (OOS) window for those fixed
  params. **Consistency = share of the 6 OOS windows with a positive return.**
* **Warm-up**: each segment is prepended with up to 60 *real* bars of history
  so slow indicators (EMA 50, Donchian 15, the regime classifier) have
  something to warm up on. Those bars are past data at segment time (no
  look-ahead) and the engine skips them entirely — no trade and no equity from
  them is counted. This is why the whole grid was re-run for this revision:
  earlier numbers taken with `warmup=0` are not comparable.
* **Full-period column**: one extra backtest of the same fixed params over the
  *whole* dataset (no fold, no split), as the "would this have worked end to
  end?" check. It is a sanity check on a single price path — not evidence.
* Rows are parameter sets, not fitted models: nothing is selected using OOS
  data, and no row is dropped because it looks bad.

Reproduce: `cd backend && .venv/bin/python scripts/walkforward_sweep.py`
(prints the table and rewrites `data/results/walkforward_results.json` +
`meta.json`).

## Headline

**Two parameter sets cleared the owner's ≥70% bar** (5 of 6 OOS windows
positive) — exact parameters in "Winner" / "Runner-up" below. Both are research
candidates only: `validated` stays `False`, and paper trading plus a forward
(out-of-sample-in-time) check come next before any size is risked.

## Full results — 65 parameter sets × 6 folds each

Sorted by OOS-window consistency, then mean OOS return. `cons%` = share of the
6 OOS windows with positive return; `meanOOS` = mean return per OOS window;
`worst DD` = worst OOS max-drawdown across folds; `full` = single backtest over
the whole dataset (whole-history sanity check, no OOS split).

| family | tf | params | folds | cons% | mean OOS | worst OOS DD | OOS trades | full-period | full DD | full trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| donchian | 1d | channel=15 | 6 | 83.3% | +0.45% | 0.44% | 19 | +1.20% | 1.69% | 55 |
| rsi | 1d | overbought=70, oversold=30, period=7, stop_loss_pct=0.03, take_profit_pct=0.06 | 6 | 83.3% | +0.36% | 0.86% | 13 | -0.82% | 5.14% | 101 |
| donchian | 1d | channel=10 | 6 | 66.7% | +0.41% | 0.44% | 20 | -1.10% | 3.08% | 69 |
| donchian | 1d | channel=12 | 6 | 66.7% | +0.41% | 0.44% | 20 | -0.56% | 2.53% | 63 |
| ema | 1d | fast=5, slow=20 | 6 | 66.7% | +0.38% | 0.32% | 7 | +2.35% | 1.46% | 28 |
| donchian | 1d | channel=8 | 6 | 66.7% | +0.37% | 0.44% | 21 | -1.53% | 3.49% | 71 |
| ema | 1d | fast=5, slow=20, volume_filter_bars=10 | 6 | 66.7% | +0.33% | 0.32% | 5 | +1.57% | 2.06% | 18 |
| ema | 1d | fast=5, slow=20, volume_filter_bars=20 | 6 | 66.7% | +0.33% | 0.32% | 5 | +1.31% | 2.07% | 16 |
| donchian | 1d | channel=15, volume_filter_bars=10 | 6 | 66.7% | +0.31% | 0.52% | 17 | +0.46% | 1.95% | 52 |
| donchian | 1d | channel=15, volume_filter_bars=20 | 6 | 66.7% | +0.31% | 0.52% | 17 | +0.46% | 1.95% | 52 |
| ema | 1d | fast=10, slow=30 | 6 | 66.7% | +0.28% | 0.91% | 6 | +2.55% | 1.08% | 18 |
| donchian | 1d | channel=8, volume_filter_bars=10 | 6 | 66.7% | +0.27% | 0.66% | 18 | -1.21% | 3.38% | 60 |
| donchian | 1d | channel=8, volume_filter_bars=20 | 6 | 66.7% | +0.27% | 0.66% | 18 | -0.78% | 2.95% | 58 |
| donchian | 1d | channel=10, volume_filter_bars=10 | 6 | 66.7% | +0.27% | 0.66% | 18 | -1.21% | 3.38% | 60 |
| donchian | 1d | channel=10, volume_filter_bars=20 | 6 | 66.7% | +0.27% | 0.66% | 18 | -0.78% | 2.95% | 58 |
| donchian | 1d | channel=12, volume_filter_bars=10 | 6 | 66.7% | +0.27% | 0.66% | 18 | -0.86% | 3.03% | 58 |
| donchian | 1d | channel=12, volume_filter_bars=20 | 6 | 66.7% | +0.27% | 0.66% | 18 | -0.64% | 2.82% | 57 |
| rsi | 1d | overbought=75, oversold=25, period=7, stop_loss_pct=0.03, take_profit_pct=0.06 | 6 | 66.7% | +0.24% | 0.84% | 10 | +0.53% | 4.82% | 79 |
| rsi | 1d | overbought=65, oversold=35, period=7, stop_loss_pct=0.03, take_profit_pct=0.06 | 6 | 66.7% | +0.21% | 0.86% | 16 | -1.81% | 6.06% | 113 |
| rsi | 1d | overbought=70, oversold=30, period=5, stop_loss_pct=0.03, take_profit_pct=0.06 | 6 | 66.7% | +0.19% | 0.72% | 21 | -4.28% | 7.86% | 122 |
| rsi | 1d | overbought=70, oversold=30, period=7 | 6 | 66.7% | +0.14% | 0.68% | 16 | -0.35% | 3.13% | 129 |
| sma | 4h | fast=10, slow=30 | 6 | 66.7% | +0.12% | 0.35% | 4 | -0.37% | 1.54% | 23 |
| rsi | 1d | overbought=75, oversold=25, period=5 | 6 | 66.7% | +0.11% | 0.44% | 21 | +0.08% | 2.89% | 126 |
| rsi | 1d | overbought=70, oversold=30, period=5 | 6 | 66.7% | +0.07% | 0.66% | 28 | -2.46% | 4.92% | 156 |
| rsi | 1d | overbought=65, oversold=35, period=5 | 6 | 66.7% | +0.01% | 0.66% | 30 | -3.70% | 5.86% | 180 |
| rsi | 1h | overbought=70, oversold=30, period=14 | 6 | 66.7% | -0.07% | 1.79% | 25 | -0.12% | 2.70% | 96 |
| ema | 4h | fast=5, slow=20 | 6 | 50.0% | +0.11% | 0.55% | 6 | +0.32% | 1.34% | 32 |
| ema | 4h | fast=5, slow=20, volume_filter_bars=10 | 6 | 50.0% | +0.11% | 0.55% | 6 | +0.21% | 1.04% | 21 |
| ema | 4h | fast=5, slow=20, volume_filter_bars=20 | 6 | 50.0% | +0.11% | 0.55% | 6 | +0.57% | 1.04% | 19 |
| sma | 4h | fast=5, slow=20 | 6 | 50.0% | +0.09% | 0.22% | 5 | -0.35% | 1.90% | 31 |
| rsi | 1d | overbought=75, oversold=25, period=7 | 6 | 50.0% | +0.08% | 0.44% | 12 | +1.05% | 3.44% | 94 |
| sma | 1d | fast=10, slow=30 | 6 | 50.0% | +0.08% | 0.59% | 6 | -0.80% | 2.14% | 20 |
| sma | 4h | fast=20, slow=50 | 6 | 50.0% | +0.07% | 0.22% | 4 | +1.06% | 0.42% | 11 |
| ema | 4h | fast=20, slow=50 | 6 | 50.0% | +0.06% | 0.21% | 3 | +1.29% | 1.03% | 10 |
| ema | 4h | fast=10, slow=30 | 6 | 50.0% | +0.05% | 0.34% | 4 | -0.56% | 1.89% | 23 |
| ema | 4h | fast=10, slow=30, volume_filter_bars=20 | 6 | 50.0% | +0.05% | 0.34% | 4 | +0.67% | 1.24% | 14 |
| donchian | 4h | channel=20 | 6 | 50.0% | +0.04% | 0.24% | 5 | -0.16% | 1.98% | 27 |
| sma | 1d | fast=5, slow=20 | 6 | 50.0% | +0.04% | 0.44% | 7 | -0.76% | 1.26% | 28 |
| donchian | 4h | channel=10 | 6 | 50.0% | -0.04% | 0.44% | 8 | -1.25% | 2.71% | 42 |
| rsi | 1d | overbought=65, oversold=35, period=7 | 6 | 50.0% | -0.12% | 0.90% | 23 | -3.12% | 5.37% | 154 |
| ema | 1d | fast=10, slow=30, volume_filter_bars=10 | 6 | 33.3% | +0.19% | 0.00% | 2 | +2.33% | 0.57% | 10 |
| ema | 1d | fast=10, slow=30, volume_filter_bars=20 | 6 | 33.3% | +0.19% | 0.00% | 2 | +2.33% | 0.57% | 10 |
| rsi | 1d | overbought=70, oversold=30, period=9, stop_loss_pct=0.03, take_profit_pct=0.06 | 6 | 33.3% | +0.13% | 0.73% | 7 | -5.54% | 7.87% | 81 |
| rsi | 1d | overbought=70, oversold=30, period=9 | 6 | 33.3% | +0.07% | 0.68% | 10 | -2.51% | 4.58% | 101 |
| sma | 1d | fast=20, slow=50 | 6 | 33.3% | +0.05% | 0.23% | 4 | +0.58% | 0.65% | 11 |
| ema | 4h | fast=20, regime_filter=trending-up, slow=50 | 6 | 33.3% | +0.03% | 0.21% | 2 | +1.13% | 0.59% | 6 |
| ema | 4h | fast=10, slow=30, volume_filter_bars=10 | 6 | 33.3% | +0.02% | 0.34% | 3 | +0.97% | 0.94% | 12 |
| rsi | 1d | overbought=65, oversold=35, period=9 | 6 | 33.3% | -0.06% | 0.73% | 15 | -5.08% | 7.37% | 140 |
| rsi | 1d | overbought=75, oversold=25, period=9 | 6 | 16.7% | +0.05% | 0.46% | 5 | -2.26% | 3.69% | 73 |
| ema | 1d | fast=20, regime_filter=trending-up, slow=50 | 6 | 16.7% | +0.05% | 0.28% | 1 | +0.52% | 0.77% | 4 |
| ema | 1d | fast=20, slow=50 | 6 | 16.7% | -0.06% | 0.51% | 3 | +1.62% | 0.70% | 9 |
| sma | 1h | fast=5, slow=20 | 6 | 16.7% | -0.15% | 0.42% | 28 | -0.89% | 3.20% | 147 |
| rsi | 4h | overbought=70, oversold=30, period=14, stop_loss_pct=0.03, take_profit_pct=0.06 | 6 | 16.7% | -0.25% | 1.69% | 8 | +0.71% | 3.08% | 30 |
| rsi | 4h | overbought=70, oversold=30, period=7, stop_loss_pct=0.03, take_profit_pct=0.06 | 6 | 16.7% | -0.34% | 1.92% | 17 | +0.41% | 2.31% | 56 |
| ema | 1d | fast=5, regime_filter=trending-up, slow=20 | 6 | 0.0% | +0.00% | 0.00% | 0 | -0.32% | 0.32% | 1 |
| ema | 1d | fast=10, regime_filter=trending-up, slow=30 | 6 | 0.0% | +0.00% | 0.00% | 0 | +0.40% | 0.58% | 2 |
| ema | 4h | fast=5, regime_filter=trending-up, slow=20 | 6 | 0.0% | +0.00% | 0.00% | 0 | -0.24% | 0.24% | 1 |
| ema | 4h | fast=20, slow=50, volume_filter_bars=10 | 6 | 0.0% | +0.00% | 0.00% | 0 | +0.27% | 1.38% | 7 |
| ema | 4h | fast=20, slow=50, volume_filter_bars=20 | 6 | 0.0% | +0.00% | 0.00% | 0 | +0.27% | 1.38% | 7 |
| sma | 4h | fast=50, slow=200 | 6 | 0.0% | +0.00% | 0.00% | 0 | -0.28% | 0.76% | 4 |
| sma | 1d | fast=50, slow=200 | 6 | 0.0% | +0.00% | 0.00% | 0 | -0.06% | 0.44% | 3 |
| ema | 1d | fast=20, slow=50, volume_filter_bars=10 | 6 | 0.0% | -0.05% | 0.51% | 1 | +0.52% | 0.64% | 4 |
| ema | 1d | fast=20, slow=50, volume_filter_bars=20 | 6 | 0.0% | -0.05% | 0.51% | 1 | +0.84% | 0.32% | 3 |
| ema | 4h | fast=10, regime_filter=trending-up, slow=30 | 6 | 0.0% | -0.05% | 0.34% | 1 | -0.89% | 1.02% | 3 |
| donchian | 4h | channel=55 | 6 | 0.0% | -0.11% | 0.60% | 3 | -1.16% | 2.19% | 18 |

## Winner — Donchian 1d breakout, channel 15

```
DonchianBreakoutStrategy(channel=15, stop_loss_pct=0.02, take_profit_pct=0.05,
                         timeframe="1d")
```

Library preset: `get_strategy("donchian_1d")` (`make_donchian_1d()`), documented
in `halaltrade/strategies/__init__.py`. Rules: BUY when the daily close exceeds
the highest high of the previous 15 daily bars while flat (mandatory 2%
stop-loss, 5% take-profit); SELL only closes an owned position when the close
falls below the lowest low of the previous 15 bars. Spot, 1x, long-only.

| Metric | Value |
| --- | --- |
| OOS-window consistency | **83.3% (5 of 6 windows positive)** — meets the ≥70% goal |
| Per-window OOS returns | +0.04%, +1.26%, +0.82%, +0.26%, **−0.44%**, +0.74% |
| Mean OOS return per window | **+0.45%** |
| Worst OOS max-drawdown | 0.44% |
| OOS trades (6 windows) | 19 |
| Whole-history backtest (984 daily bars, 2024-01 → 2026-09) | **+1.20%** after 10 bps fees, 55 trades, 1.69% max drawdown |

## Runner-up — RSI(7) mean-reversion 1d

```
RsiMeanReversionStrategy(period=7, oversold=30, overbought=70,
                         stop_loss_pct=0.03, take_profit_pct=0.06,
                         timeframe="1d")
```

Library preset: `get_strategy("rsi_mean_reversion_1d")`.

| Metric | Value |
| --- | --- |
| OOS-window consistency | 83.3% (5 of 6) |
| Per-window OOS returns | +0.70%, +0.58%, +0.58%, **−0.40%**, +0.56%, +0.16% |
| Mean OOS return per window | +0.36% |
| Worst OOS max-drawdown | 0.86% |
| OOS trades | 13 |
| Whole-history backtest | **−0.82%** (101 trades, 5.14% max drawdown) |

It clears the consistency bar but loses money over the full history, so it is
*not* the pick.

## Honest caveats (read before believing the winner)

1. **The margin over its neighbours is one essentially-flat window.**
   Donchian 1d channels 8, 10 and 12 all land at 66.7% (4/6) with the *same*
   mean OOS (+0.37…+0.41%) and the same worst drawdown (0.44%); channel 15
   differs only in window 1, where it returns +0.04% instead of −0.18%. The
   Donchian-1d *family* is consistently mildly positive out-of-sample, but
   "channel 15 specifically" is not a strongly separated edge — treat the
   5/6 as family evidence with a thin parameter margin. Only channel 15 also
   comes out positive over the whole history (+1.20%); channels 8/10/12 are
   −1.5%/−1.1%/−0.6% end-to-end.
2. **The magnitudes are small.** +0.45% mean per ~33-day OOS window on a
   500 USDT clip is a thin edge that fees, slippage or a bad stretch of market
   can erase. Nothing here justifies large size, and nothing auto-sizes: the
   owner still sets every trade size.
3. **6 folds is a small sample.** 83.3% is 5 of 6 — one window flips it to
   66.7%. The 4-fold numbers that looked like 75–100% in earlier revisions were
   exactly this effect, which is why the sweep stays at 6 folds.
4. **Multiple comparisons.** 65 parameter sets were tested; at 5/6 the winner
   is the best of many draws. This is selection risk, not proof.
5. **Warm-up changed the numbers.** This revision prepends 60 real prior bars
   per segment so slow strategies can trade at all; the whole grid was re-run
   under that setting, and it is not comparable to `warmup=0` numbers quoted in
   earlier revisions.
6. **Overlapping/trending market.** The 1d sample covers one bull-heavy regime
   (2024-01 → 2026-09). Long-only breakout strategies flatter such a period.

## What the filters showed

* **Volume confirmation (last bar's volume above the mean of the prior 10/20
  bars)** is roughly neutral-to-negative on 1d: it cuts trades (Donchian 15:
  19 → 17 OOS trades) and slightly lowers consistency (83.3% → 66.7%) with
  mildly lower mean OOS (+0.45% → +0.31%). On EMA 1d 5/20 it costs trades with
  the same 66.7%. No row improved past the winner because of it.
* **Regime filter (`trending-up`)** produced almost no trades at all (0 trades
  in 4 of 7 rows, 1–2 in the rest). Reason: `research.regime.classify_regime`
  gives *high-volatility* priority over trend direction, and BTC daily bars sit
  above the 3% ATR/close threshold a lot of the time — so "trending-up" rarely
  fires. As configured today the filter is far too restrictive to be useful;
  that is a finding about the filter, not a licence to loosen a gate (the
  regime filter is research-only entry gating, not a policy gate).
* **Trend-following on 1d (EMA 5/20)** is the best full-history row
  (+2.35%, 28 trades) but only 66.7% window consistency — i.e. the end-to-end
  result and the per-window consistency do not agree, which is precisely why
  both columns are reported.
* **Timeframes**: 1h results are weak (RSI 14 1h at 66.7% consistency but
  −0.07% mean OOS and 1.79% drawdown; SMA 1h 16.7%). 4h sits in the middle.
  Daily is where the signal is.

## Verdict against the owner's goal

* **Goal (≥70% of windows positive): MET** by Donchian 1d channel 15 at 83.3%
  (5/6), and by RSI(7) 1d 30/70 with 0.03/0.06 at 83.3% (5/6).
* **Winner: Donchian 1d channel 15** — best consistency, best mean OOS, lowest
  drawdown, and the only top row that is also positive over the full history.
* **Status: research candidate — NOT validated, NOT advice, NOT live.** The
  next honest steps are paper trading with the same params, then a forward test
  on data that did not exist when the params were chosen. `validated` stays
  `False` in the library either way; no sweep output can change a policy gate.
