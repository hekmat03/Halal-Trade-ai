# Run this in Colab, in the SAME notebook, AFTER your pytest cell (repo already cloned).
# Fetches REAL BTC/USDT historical candles from Binance's public API (no key needed)
# and runs an ACTUAL backtest — real numbers, not simulated.

import requests
from datetime import datetime, timezone
from halaltrade.marketdata.models import Candle
from halaltrade.backtest.engine import BacktestEngine, BacktestConfig
from halaltrade.backtest.strategies import make_ema_trend, make_rsi_mean_reversion, make_donchian_breakout

def fetch_binance_klines(symbol="BTCUSDT", interval="1h", limit=1000):
    """Real historical OHLCV data from Binance's public REST API."""
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    candles = []
    for row in raw:
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

print("Fetching real BTC/USDT candles from Binance...")
candles = fetch_binance_klines(symbol="BTCUSDT", interval="1h", limit=1000)
print(f"Got {len(candles)} real candles, from {candles[0].timestamp} to {candles[-1].timestamp}")

engine = BacktestEngine()
config = BacktestConfig(starting_equity=5000.0, trade_amount=500.0)

strategies = {
    "EMA Trend (12/26)": make_ema_trend(),
    "RSI Mean Reversion": make_rsi_mean_reversion(),
    "Donchian Breakout (20)": make_donchian_breakout(),
}

for name, strat in strategies.items():
    result = engine.run(strat, candles, config, strategy_name=name)
    r = result.report
    print(f"\n=== {name} ===")
    print(f"Total trades:      {r.trade_count}")
    print(f"Win rate:          {r.win_rate:.1%}")
    print(f"Total return:      {r.total_return:.2%}")
    print(f"P&L:               ${r.pnl:.2f}")
    print(f"Max drawdown:      {r.max_drawdown:.2%} (${r.max_drawdown_usd:.2f})")
    print(f"Profit factor:     {r.profit_factor:.2f}")
    print(f"Sharpe ratio:      {r.sharpe:.2f}")
    print(f"Wins / Losses:     {r.wins} / {r.losses}")
    print(f"Fees paid:         ${r.fees_paid:.2f}")
