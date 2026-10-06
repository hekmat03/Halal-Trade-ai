# Run in Colab: !python run_volume_breakout_test.py
# Tests whether volume confirmation gives Donchian breakout a real edge,
# using DAILY BTC data (16-fold walk-forward, same rigor as the Donchian
# re-check) and comparing against Buy & Hold.

import requests
from datetime import datetime, timezone
from halaltrade.marketdata.models import Candle
from halaltrade.backtest.engine import BacktestEngine, BacktestConfig
from halaltrade.backtest.strategies import make_donchian_breakout, make_volume_confirmed_breakout
from halaltrade.validation.walkforward import generate_walk_forward_windows
from halaltrade.validation.benchmark import buy_and_hold_return, compare_to_benchmark


def fetch_binance_daily_klines(symbol="BTCUSDT", interval="1d", total_candles=1000):
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


print("Fetching real DAILY BTC/USDT candles from Binance...")
candles = fetch_binance_daily_klines(total_candles=1000)
print(f"Total: {len(candles)} daily candles, from {candles[0].timestamp.date()} to {candles[-1].timestamp.date()}\n")

engine = BacktestEngine()
config = BacktestConfig(starting_equity=5000.0, trade_amount=500.0)
PERIODS_PER_YEAR_DAILY = 365.0
TRAIN_SIZE = 110
TEST_SIZE = 55

windows = generate_walk_forward_windows(len(candles), TRAIN_SIZE, TEST_SIZE)
print(f"Generated {len(windows)} walk-forward windows.\n")

variants = {
    "Donchian (no volume filter, baseline)": lambda: make_donchian_breakout(channel_period=15),
    "Volume-Confirmed Breakout (1.5x)": lambda: make_volume_confirmed_breakout(channel_period=15, volume_mult=1.5),
    "Volume-Confirmed Breakout (2.0x, stricter)": lambda: make_volume_confirmed_breakout(channel_period=15, volume_mult=2.0),
}

summary = []
for name, factory in variants.items():
    print(f"\n{'='*70}\n{name}\n{'='*70}")
    profitable = 0
    beats_benchmark = 0
    returns = []
    total_trades = 0
    for i, window in enumerate(windows, 1):
        test_slice = candles[window.test_start:window.test_end]
        result = engine.run(factory(), test_slice, config, strategy_name=name)
        r = result.report
        closes = [c.close for c in test_slice]
        bh = buy_and_hold_return(closes, periods_per_year=PERIODS_PER_YEAR_DAILY)
        verdict = compare_to_benchmark(r.total_return, r.sharpe, bh)
        if r.pnl > 0:
            profitable += 1
        if "PASSES" in verdict:
            beats_benchmark += 1
        returns.append(r.total_return)
        total_trades += r.trade_count
        start_date = test_slice[0].timestamp.date()
        end_date = test_slice[-1].timestamp.date()
        print(f"Window {i} [{start_date} to {end_date}]: trades={r.trade_count}, "
              f"return={r.total_return:+.2%}, B&H={bh.total_return:+.2%}, {verdict.split(':')[0]}")

    n = len(windows)
    avg_return = sum(returns) / n if n else 0.0
    print(f"\n-> {profitable}/{n} windows profitable ({profitable/n:.0%} consistency)")
    print(f"-> Beat Buy&Hold in {beats_benchmark}/{n} windows")
    print(f"-> Mean return per window: {avg_return:+.2%}, total trades: {total_trades}")
    summary.append((name, profitable / n, avg_return, total_trades))

print(f"\n\n{'='*70}\nSUMMARY (sorted by consistency)\n{'='*70}")
for name, consistency, avg_return, total_trades in sorted(summary, key=lambda x: -x[1]):
    print(f"{name:48s} consistency={consistency:.0%}  avg_return={avg_return:+.2%}  trades={total_trades}")
