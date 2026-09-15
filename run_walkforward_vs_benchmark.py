# Run in Colab: !python run_walkforward_vs_benchmark.py
# Same 3 strategies as before, but now compared against simple Buy & Hold
# on the SAME windows -- answers: were our strategies actually bad, or was
# BTC itself flat/down during these periods too?

import requests
from datetime import datetime, timezone
from halaltrade.marketdata.models import Candle
from halaltrade.backtest.engine import BacktestEngine, BacktestConfig
from halaltrade.backtest.strategies import make_ema_trend, make_rsi_mean_reversion, make_donchian_breakout
from halaltrade.validation.walkforward import run_walk_forward
from halaltrade.validation.benchmark import buy_and_hold_return, compare_to_benchmark


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
PERIODS_PER_YEAR_1H = 8760.0

strategies = {
    "EMA Trend": lambda: make_ema_trend(),
    "RSI Mean Reversion": lambda: make_rsi_mean_reversion(),
    "Donchian Breakout": lambda: make_donchian_breakout(),
}

for name, factory in strategies.items():
    print(f"\n{'='*70}\n{name}  (vs Buy & Hold on the SAME windows)\n{'='*70}")
    results = run_walk_forward(
        engine, factory, candles, config,
        train_size=TRAIN_SIZE, test_size=TEST_SIZE, strategy_name=name,
    )
    if not results:
        print("Not enough data.")
        continue

    strategy_beats_benchmark_count = 0
    for i, (window, result) in enumerate(results, 1):
        r = result.report
        test_closes = [c.close for c in candles[window.test_start:window.test_end]]
        bh = buy_and_hold_return(test_closes, periods_per_year=PERIODS_PER_YEAR_1H)
        verdict = compare_to_benchmark(r.total_return, r.sharpe, bh)
        if "PASSES" in verdict:
            strategy_beats_benchmark_count += 1
        start_date = candles[window.test_start].timestamp.date()
        end_date = candles[window.test_end - 1].timestamp.date()
        print(f"Window {i} [{start_date} to {end_date}]: {verdict}")

    n = len(results)
    print(f"\n-> Strategy beat Buy&Hold in {strategy_beats_benchmark_count}/{n} windows "
          f"({strategy_beats_benchmark_count/n:.0%})")
