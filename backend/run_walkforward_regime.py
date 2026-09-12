# Run in Colab: !python run_walkforward_regime.py
# Compares EMA Trend with vs WITHOUT regime filtering, on real BTC/USDT data.

import requests
from datetime import datetime, timezone
from halaltrade.marketdata.models import Candle
from halaltrade.backtest.engine import BacktestEngine, BacktestConfig
from halaltrade.backtest.strategies import make_ema_trend, make_regime_filtered
from halaltrade.regime.detector import MarketRegime
from halaltrade.validation.walkforward import run_walk_forward


def fetch_binance_klines_paginated(symbol="BTCUSDT", interval="1h", total_candles=4000):
    url = "https://data-api.binance.vision/api/v3/klines"
    all_rows = []
    end_time = None
    while len(all_rows) < total_candles:
        params = {"symbol": symbol, "interval": interval, "limit": 1000}
        if end_time is not None:
            params["endTime"] = end_time
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_rows = batch + all_rows
        end_time = batch[0][0] - 1
        print(f"  fetched {len(all_rows)} candles so far...")
    all_rows = all_rows[-total_candles:]
    candles = []
    for row in all_rows:
        candles.append(Candle(
            symbol=symbol, timeframe=interval,
            open=float(row[1]), high=float(row[2]), low=float(row[3]),
            close=float(row[4]), volume=float(row[5]),
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
            source="binance",
        ))
    return candles


print("Fetching ~4000 hourly candles of real BTC/USDT data...")
candles = fetch_binance_klines_paginated(total_candles=4000)
print(f"Total: {len(candles)} candles, from {candles[0].timestamp} to {candles[-1].timestamp}\n")

engine = BacktestEngine()
config = BacktestConfig(starting_equity=5000.0, trade_amount=500.0)

TRAIN_SIZE = 1000
TEST_SIZE = 500

EMA_FAST, EMA_SLOW, ATR_PERIOD = 12, 26, 14

variants = {
    "EMA Trend (unfiltered)": lambda: make_ema_trend(fast=EMA_FAST, slow=EMA_SLOW, atr_period=ATR_PERIOD),
    "EMA Trend + Regime Filter (uptrends only)": lambda: make_regime_filtered(
        make_ema_trend(fast=EMA_FAST, slow=EMA_SLOW, atr_period=ATR_PERIOD),
        allowed_regimes={MarketRegime.STRONG_UPTREND, MarketRegime.WEAK_UPTREND},
        fast_period=EMA_FAST, slow_period=EMA_SLOW, atr_period=ATR_PERIOD,
    ),
    "EMA Trend + Regime Filter (strong uptrend only)": lambda: make_regime_filtered(
        make_ema_trend(fast=EMA_FAST, slow=EMA_SLOW, atr_period=ATR_PERIOD),
        allowed_regimes={MarketRegime.STRONG_UPTREND},
        fast_period=EMA_FAST, slow_period=EMA_SLOW, atr_period=ATR_PERIOD,
    ),
}

for name, factory in variants.items():
    print(f"\n{'='*60}\n{name}\n{'='*60}")
    results = run_walk_forward(
        engine, factory, candles, config,
        train_size=TRAIN_SIZE, test_size=TEST_SIZE, strategy_name=name,
    )
    if not results:
        print("Not enough data for even one walk-forward window.")
        continue

    total_return_sum = 0.0
    profitable_windows = 0
    total_trades = 0
    for i, (window, result) in enumerate(results, 1):
        r = result.report
        total_return_sum += r.total_return
        total_trades += r.trade_count
        if r.pnl > 0:
            profitable_windows += 1
        start_date = candles[window.test_start].timestamp.date()
        end_date = candles[window.test_end - 1].timestamp.date()
        print(f"Window {i} [{start_date} to {end_date}]: "
              f"trades={r.trade_count}, return={r.total_return:.2%}, "
              f"win_rate={r.win_rate:.1%}, profit_factor={r.profit_factor:.2f}")

    n = len(results)
    print(f"\n-> {profitable_windows}/{n} windows profitable ({profitable_windows/n:.0%} consistency), "
          f"avg return/window={total_return_sum/n:.2%}, total trades={total_trades}")