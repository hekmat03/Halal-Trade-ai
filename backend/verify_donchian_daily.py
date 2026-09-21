# Run in Colab: !python verify_donchian_daily.py
# Independently re-checks the claimed "Donchian breakout, 1d, channel=15"
# result using OUR OWN BacktestEngine + walk-forward + Buy&Hold benchmark --
# not trusting another AI's numbers without re-deriving them ourselves.

import requests
from datetime import datetime, timezone
from halaltrade.marketdata.models import Candle
from halaltrade.backtest.engine import BacktestEngine, BacktestConfig
from halaltrade.backtest.strategies import make_donchian_breakout
from halaltrade.validation.walkforward import generate_walk_forward_windows
from halaltrade.validation.benchmark import buy_and_hold_return, compare_to_benchmark


def fetch_binance_daily_klines(symbol="BTCUSDT", interval="1d", total_candles=1000):
    """Daily candles -- 1000 days is ~2.7 years, covers roughly the same
    span CTO.new's report used (2024-01 to now)."""
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

# Their exact winning parameters: channel=15, 2% stop-loss, 5% take-profit, daily.
strategy_factory = lambda: make_donchian_breakout(channel_period=15, atr_period=14)

# Walk-forward: 6 folds like their report, on daily bars this time.
TRAIN_SIZE = 110
TEST_SIZE = 55

windows = generate_walk_forward_windows(len(candles), TRAIN_SIZE, TEST_SIZE)
print(f"Generated {len(windows)} walk-forward windows on daily data.\n")

print(f"{'='*70}\nOUR independent re-check: Donchian 1d, channel=15\n{'='*70}")
profitable = 0
beats_benchmark = 0
returns = []
for i, window in enumerate(windows, 1):
    test_slice = candles[window.test_start:window.test_end]
    result = engine.run(strategy_factory(), test_slice, config, strategy_name="donchian_1d_15")
    r = result.report
    closes = [c.close for c in test_slice]
    bh = buy_and_hold_return(closes, periods_per_year=PERIODS_PER_YEAR_DAILY)
    verdict = compare_to_benchmark(r.total_return, r.sharpe, bh)
    if r.pnl > 0:
        profitable += 1
    if "PASSES" in verdict:
        beats_benchmark += 1
    returns.append(r.total_return)
    start_date = test_slice[0].timestamp.date()
    end_date = test_slice[-1].timestamp.date()
    print(f"Window {i} [{start_date} to {end_date}]: trades={r.trade_count}, "
          f"return={r.total_return:+.2%}, B&H={bh.total_return:+.2%}, {verdict.split(':')[0]}")

n = len(windows)
avg_return = sum(returns) / n if n else 0.0
print(f"\n-> {profitable}/{n} windows profitable ({profitable/n:.0%} consistency)")
print(f"-> Beat Buy&Hold in {beats_benchmark}/{n} windows")
print(f"-> Mean return per window: {avg_return:+.2%}")
print(f"\nClaimed by the other report: 5/6 (83.3%) consistency, +0.45% mean OOS.")
print(f"NOTE: window count/boundaries differ from theirs (train/test split")
print(f"sizes aren't identical), so exact numbers won't match -- but the overall")
print(f"CONSISTENCY LEVEL should be roughly comparable if the effect is real.")

# Full-history single backtest, like their "sanity check" column.
full_result = engine.run(strategy_factory(), candles, config, strategy_name="donchian_1d_15_full")
fr = full_result.report
print(f"\nFull-history backtest ({len(candles)} days): return={fr.total_return:+.2%}, "
      f"trades={fr.trade_count}, max_drawdown={fr.max_drawdown:.2%}")
print(f"Claimed by the other report: +1.20%, 55 trades, 1.69% max drawdown.")

