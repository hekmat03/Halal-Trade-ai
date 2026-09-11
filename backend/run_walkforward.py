# Run in Colab: !python run_walkforward.py
# Pulls MONTHS of real BTC/USDT data (paginated, since Binance caps 1000/request)
# and runs genuine walk-forward validation (train/test windows) per strategy.

import requests
from datetime import datetime, timezone
from halaltrade.marketdata.models import Candle
from halaltrade.backtest.engine import BacktestEngine, BacktestConfig
from halaltrade.backtest.strategies import make_ema_trend, make_rsi_mean_reversion, make_donchian_breakout
from halaltrade.validation.walkforward import generate_walk_forward_windows, run_walk_forward


def fetch_binance_klines_paginated(symbol="BTCUSDT", interval="1h", total_candles=4000):
    """Fetch more than 1000 candles by paging backwards from now."""
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
        end_time = batch[0][0] - 1  # page further back in time
        print(f"  fetched {len(all_rows)} candles so far...")

    all_rows = all_rows[-total_candles:]  # trim to exactly what we asked for
    candles = []
    for row in all_rows:
        candles.append(Candle(
            symbol=symbol,
            timeframe=interval,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            timestamp=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
            source="binance",
        ))
    return candles


print("Fetching ~4000 hourly candles (~5.5 months) of real BTC/USDT data...")
candles = fetch_binance_klines_paginated(symbol="BTCUSDT", interval="1h", total_candles=4000)
print(f"Total: {len(candles)} candles, from {candles[0].timestamp} to {candles[-1].timestamp}\n")

engine = BacktestEngine()
config = BacktestConfig(starting_equity=5000.0, trade_amount=500.0)

TRAIN_SIZE = 1000  # ~6 weeks of hourly bars
TEST_SIZE = 500    # ~3 weeks of hourly bars, out-of-sample each time

strategies = {
    "EMA Trend (12/26)": lambda: make_ema_trend(),
    "RSI Mean Reversion": lambda: make_rsi_mean_reversion(),
    "Donchian Breakout (20)": lambda: make_donchian_breakout(),
}

for name, factory in strategies.items():
    print(f"\n{'='*50}\n{name}\n{'='*50}")
    results = run_walk_forward(
        engine, factory, candles, config,
        train_size=TRAIN_SIZE, test_size=TEST_SIZE, strategy_name=name,
    )
    if not results:
        print("Not enough data for even one walk-forward window.")
        continue

    total_return_sum = 0.0
    profitable_windows = 0
    for i, (window, result) in enumerate(results, 1):
        r = result.report
        total_return_sum += r.total_return
        if r.pnl > 0:
            profitable_windows += 1
        start_date = candles[window.test_start].timestamp.date()
        end_date = candles[window.test_end - 1].timestamp.date()
        print(f"Window {i} [{start_date} to {end_date}]: "
              f"trades={r.trade_count}, return={r.total_return:.2%}, "
              f"win_rate={r.win_rate:.1%}, profit_factor={r.profit_factor:.2f}, "
              f"max_dd={r.max_drawdown:.2%}")

    n = len(results)
    print(f"\n-> {profitable_windows}/{n} windows profitable "
          f"({profitable_windows/n:.0%} consistency), "
          f"avg return/window={total_return_sum/n:.2%}")