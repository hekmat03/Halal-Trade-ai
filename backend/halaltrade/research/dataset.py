"""Real Binance kline dataset loading (Delivery 6 research).

Loads **real** BTC/USDT historical klines from the public
``data.binance.vision`` archive (monthly zips; daily zips as fallback for the
current month), parses them into ``Candle`` objects, and persists them as
JSONL under ``backend/data/`` (gitignored). Used only for research
(walk-forward sweeps); never feeds live execution.

The live Binance REST API is geo-blocked (HTTP 451) from this host, so the
vision archive is the real-data source of choice — it is Binance's own public
history bucket. No synthetic data is ever substituted; if data cannot be
loaded the loader raises ``DataUnavailableError`` instead of inventing rows.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..marketdata.models import Candle

logger = logging.getLogger(__name__)

__all__ = ["fetch_klines_vision", "load_candles", "save_candles", "DataUnavailableError"]

VISION_BASE = "https://data.binance.vision/data/spot"
SYMBOL = "BTCUSDT"

# data.binance.vision kline CSV column order (monthly + daily share it).
_HEADER = 12  # open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore


class DataUnavailableError(RuntimeError):
    """Raised when real data cannot be fetched/parsed — never fabricate."""


def _monthly_url(interval: str, yyyymm: str) -> str:
    return f"{VISION_BASE}/monthly/klines/{SYMBOL}/{interval}/{SYMBOL}-{interval}-{yyyymm}.zip"


def _daily_url(interval: str, date: str) -> str:
    return f"{VISION_BASE}/daily/klines/{SYMBOL}/{interval}/{SYMBOL}-{interval}-{date}.zip"


def _iter_csv_rows(zip_bytes: bytes) -> list[list[str]]:
    """Extract the single CSV inside a vision zip and return its rows."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            name = zf.namelist()[0]
            with zf.open(name) as fh:
                text = fh.read().decode("utf-8")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
        raise DataUnavailableError(f"bad vision zip payload: {exc}") from exc
    rows = list(csv.reader(io.StringIO(text)))
    return [r for r in rows if r and len(r) >= _HEADER]


def _parse_rows(rows: list[list[str]], interval: str) -> list[Candle]:
    candles: list[Candle] = []
    for r in rows:
        try:
            open_ms = int(r[0])
            # vision switched to microsecond timestamps (16 digits) in
            # 2023; older rows are milliseconds (13 digits). Normalize.
            if open_ms >= 10**14:
                open_ms //= 1000
            candles.append(
                Candle(
                    symbol=SYMBOL,
                    timeframe=interval,
                    open=float(r[1]),
                    high=float(r[2]),
                    low=float(r[3]),
                    close=float(r[4]),
                    volume=float(r[5]),
                    timestamp=datetime.fromtimestamp(open_ms / 1000.0, tz=timezone.utc),
                    source="binance_vision",
                    raw={"open_time_ms": open_ms, "close_time_ms": int(r[6]),
                         "quote_volume": float(r[7]), "count": int(r[8])},
                )
            )
        except (ValueError, IndexError, OverflowError, OSError) as exc:
            raise DataUnavailableError(f"malformed kline row: {r[:6]!r}") from exc
    return candles


def _months_between(start: datetime, end: datetime) -> list[str]:
    out: list[str] = []
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur <= end:
        out.append(cur.strftime("%Y-%m"))
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    return out


def fetch_klines_vision(
    interval: str,
    start: datetime,
    end: datetime,
    client: httpx.Client | None = None,
) -> tuple[list[Candle], list[str]]:
    """Fetch real klines from data.binance.vision for [start, end).

    Returns ``(candles, sources_used)``. Candles are sorted by timestamp,
    deduplicated, and guaranteed real (raises ``DataUnavailableError``
    otherwise). The current month uses the daily archive when the monthly zip
    is not yet published.
    """
    own = client is None
    http = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        all_rows: list[list[str]] = []
        sources: list[str] = []
        for yyyymm in _months_between(start, end):
            url = _monthly_url(interval, yyyymm)
            resp = http.get(url)
            if resp.status_code == 200:
                all_rows.extend(_iter_csv_rows(resp.content))
                sources.append(url)
                continue
            # Monthly zip missing (usually the current month) -> daily fallback.
            day = datetime.strptime(yyyymm, "%Y-%m").replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
            next_month = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
            d = day
            got = 0
            while d < next_month and d < end:
                u = _daily_url(interval, d.strftime("%Y-%m-%d"))
                rr = http.get(u)
                if rr.status_code == 200:
                    all_rows.extend(_iter_csv_rows(rr.content))
                    got += 1
                d += timedelta(days=1)
            if got:
                sources.append(f"{yyyymm} (daily x{got})")
            else:
                logger.warning("no vision data for %s %s", interval, yyyymm)
    finally:
        if own:
            http.close()

    candles = _parse_rows(all_rows, interval)
    candles.sort(key=lambda c: c.timestamp)
    deduped: list[Candle] = []
    seen: set[int] = set()
    for c in candles:
        key = int(c.timestamp.timestamp())
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped, sources


def save_candles(candles: list[Candle], path: Path) -> None:
    """Persist candles as JSONL (one Candle JSON per line)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for c in candles:
            fh.write(c.model_dump_json() + "\n")


def load_candles(path: Path | str) -> list[Candle]:
    """Load candles saved by :func:`save_candles`."""
    p = Path(path)
    if not p.exists():
        raise DataUnavailableError(f"dataset not found: {p}")
    out: list[Candle] = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(Candle.model_validate_json(line))
    return out