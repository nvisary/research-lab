"""Load OHLCV from parquet partitions into pandas DataFrames."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "bybit" / "perp" / "1m"
FUNDING_ROOT = Path(__file__).resolve().parents[1] / "data" / "bybit" / "perp" / "funding"


def _months_between(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[int, int]]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def load(symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp,
         tf: str = "1min") -> pd.DataFrame:
    """Load OHLCV for `symbol` in [start, end), resampled to `tf`.

    Returns DataFrame indexed by tz-aware UTC timestamp with columns
    [open, high, low, close, volume].
    """
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    start = start.tz_convert("UTC") if start.tzinfo else start.tz_localize("UTC")
    end = end.tz_convert("UTC") if end.tzinfo else end.tz_localize("UTC")
    parts = []
    for y, m in _months_between(start, end):
        p = DATA_ROOT / symbol / f"{y:04d}-{m:02d}.parquet"
        if p.exists():
            parts.append(pd.read_parquet(p))
    if not parts:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.concat(parts, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[(df.index >= start) & (df.index < end)]
    df = df[["open", "high", "low", "close", "volume"]]

    if tf != "1min":
        df = df.resample(tf).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna(subset=["open"])
    return df


def load_many(symbols: list[str], start: str | pd.Timestamp, end: str | pd.Timestamp,
              tf: str = "1min") -> dict[str, pd.DataFrame]:
    return {s: load(s, start, end, tf) for s in symbols}


def load_funding(symbol: str, start: str | pd.Timestamp,
                 end: str | pd.Timestamp) -> pd.DataFrame:
    """Funding-rate history for `symbol` in [start, end).

    Returns a DataFrame indexed by tz-aware UTC timestamp with one column `rate`.
    Empty DataFrame if no funding parquets are present (e.g. funding never downloaded).
    """
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    start = start.tz_convert("UTC") if start.tzinfo else start.tz_localize("UTC")
    end = end.tz_convert("UTC") if end.tzinfo else end.tz_localize("UTC")
    parts = []
    for y, m in _months_between(start, end):
        p = FUNDING_ROOT / symbol / f"{y:04d}-{m:02d}.parquet"
        if p.exists():
            parts.append(pd.read_parquet(p))
    if not parts:
        return pd.DataFrame(columns=["rate"], index=pd.DatetimeIndex([], tz="UTC", name="timestamp"))
    df = pd.concat(parts, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[(df.index >= start) & (df.index < end)]
    return df[["rate"]]


def available_symbols() -> list[str]:
    if not DATA_ROOT.exists():
        return []
    return sorted(p.name for p in DATA_ROOT.iterdir() if p.is_dir())
