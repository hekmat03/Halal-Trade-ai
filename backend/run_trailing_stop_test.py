# Run in Colab: !python run_trailing_stop_test.py
# Tests whether a trailing stop lets Donchian Breakout capture more of a
# strong rally, instead of exiting early or never having a real exit.

import requests
from datetime import datetime, timezone
from halaltrade.marketdata.models import Candle
from halaltrade.backtest.engine import BacktestEngine, BacktestConfig
from halaltrade.backtest.strategies import make_donchian_breakout
from halaltrade.positions import TrailingConfig
from halaltrade.validation.walkforward import generate_walk_forward_windows
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
TRAIN_SIZE = 1000
TEST_SIZE = 500
PERIODS_PER_YEAR_1H = 8760.0

windows = generate_walk_forward_windows(len(candles), TRAIN_SIZE, TEST_SIZE)

for use_trailing in (False, True):
    for exit_on_break in (True, False):
        if not use_trailing and not exit_on_break:
            continue  # no exit mechanism at all except stop-loss -- skip, too extreme to be meaningful
        label = (
            f"Donchian (trailing={use_trailing}, own_exit={exit_on_break})"
        )
        print(f"\n{'='*70}\n{label}\n{'='*70}")

        total_beats = 0
        for window in windows:
            test_slice = candles[window.test_start:window.test_end]
            start_price = test_slice[0].close
            cfg = BacktestConfig(
                starting_equity=5000.0,
                trade_amount=500.0,
                trailing=TrailingConfig(enabled=True, distance=start_price * 0.03) if use_trailing else None,
            )
            strat = make_donchian_breakout(channel_period=20, exit_on_channel_break=exit_on_break)
            result = engine.run(strat, test_slice, cfg, strategy_name=label)
            r = result.report
            closes = [c.close for c in test_slice]
            bh = buy_and_hold_return(closes, periods_per_year=PERIODS_PER_YEAR_1H)
            verdict = compare_to_benchmark(r.total_return, r.sharpe, bh)
            if "PASSES" in verdict:
                total_beats += 1
            start_date = test_slice[0].timestamp.date()
            end_date = test_slice[-1].timestamp.date()
            print(f"Window [{start_date} to {end_date}]: trades={r.trade_count}, "
                  f"return={r.total_return:+.2%}, B&H={bh.total_return:+.2%}, {verdict.split(':')[0]}")

        print(f"\n-> Beat Buy&Hold in {total_beats}/{len(windows)} windows")
