"""Load OHLCV from parquet partitions into pandas DataFrames."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def data_root() -> Path:
    """Project data directory.

    Override location with the ``RESEARCHLAB_DATA_ROOT`` environment variable
    (e.g. mount on a separate fast disk). Defaults to ``<repo>/data``.
    """
    env_root = os.environ.get("RESEARCHLAB_DATA_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1] / "data"


DATA_ROOT = data_root() / "bybit" / "perp" / "1m"
FUNDING_ROOT = data_root() / "bybit" / "perp" / "funding"
OPEN_INTEREST_ROOT = data_root() / "bybit" / "perp" / "open_interest"


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
    return {s: load_with_open_interest(s, start, end, tf) for s in symbols}


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


def load_open_interest(symbol: str, start: str | pd.Timestamp,
                       end: str | pd.Timestamp) -> pd.DataFrame:
    """Open-interest history for `symbol` in [start, end).

    Returns a DataFrame indexed by tz-aware UTC timestamp with one column
    `open_interest`. Empty DataFrame if no OI parquets are present.
    """
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    start = start.tz_convert("UTC") if start.tzinfo else start.tz_localize("UTC")
    end = end.tz_convert("UTC") if end.tzinfo else end.tz_localize("UTC")
    parts = []
    for y, m in _months_between(start, end):
        p = OPEN_INTEREST_ROOT / symbol / f"{y:04d}-{m:02d}.parquet"
        if p.exists():
            parts.append(pd.read_parquet(p))
    if not parts:
        return pd.DataFrame(
            columns=["open_interest"],
            index=pd.DatetimeIndex([], tz="UTC", name="timestamp"),
        )
    df = pd.concat(parts, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[(df.index >= start) & (df.index < end)]
    return df[["open_interest"]]


def load_with_open_interest(symbol: str, start: str | pd.Timestamp,
                            end: str | pd.Timestamp, tf: str = "1min") -> pd.DataFrame:
    """Load OHLCV and attach stale-safe open interest when present.

    Bybit OI is sampled at a coarser cadence than 1m OHLCV. Forward-fill to the
    bar grid so every decision bar sees the latest known value at or before it.
    """
    df = load(symbol, start, end, tf=tf)
    if df.empty:
        return df
    oi = load_open_interest(symbol, start, end)
    if oi.empty:
        return df
    out = df.copy()
    out["open_interest"] = oi["open_interest"].reindex(out.index, method="ffill")
    return out


def available_symbols() -> list[str]:
    if not DATA_ROOT.exists():
        return []
    return sorted(p.name for p in DATA_ROOT.iterdir() if p.is_dir())
