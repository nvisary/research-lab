"""Read captured microstructure sessions back into pandas DataFrames.

A session on disk looks like::

    data/bybit/micro/sessions/<session_id>/
        manifest.json
        <stream>/<SYMBOL>/part-*.parquet

Typical use in a notebook::

    from microstructure import loader
    loader.list_sessions()                       # what's on disk
    ob = loader.load("2026-07-08T18-53-26Z", "orderbook", "BTCUSDT")
    tr = loader.load(loader.latest_session(), "trades", "BTCUSDT")

Timestamps are returned as int64 milliseconds; use `with_datetime=True` to also
get a UTC `dt` column indexed for convenience.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from datafeed.loader import data_root


def sessions_root() -> Path:
    return data_root() / "bybit" / "micro" / "sessions"


def list_sessions() -> list[str]:
    root = sessions_root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def latest_session() -> str:
    sessions = list_sessions()
    if not sessions:
        raise FileNotFoundError(f"no sessions under {sessions_root()}")
    return sessions[-1]


def manifest(session_id: str) -> dict:
    path = sessions_root() / session_id / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


def symbols(session_id: str, stream: str) -> list[str]:
    d = sessions_root() / session_id / stream
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def load(session_id: str, stream: str, symbol: str,
         with_datetime: bool = False) -> pd.DataFrame:
    """Concatenate all part files for one (session, stream, symbol), time-sorted."""
    d = sessions_root() / session_id / stream / symbol.upper()
    if not d.exists():
        raise FileNotFoundError(d)
    parts = sorted(d.glob("part-*.parquet"))
    if not parts:
        return pd.DataFrame()
    df = pd.concat((pd.read_parquet(p) for p in parts), ignore_index=True)
    if "ts_local" in df.columns:
        df = df.sort_values("ts_local").reset_index(drop=True)
    if with_datetime and "ts_local" in df.columns:
        df["dt"] = pd.to_datetime(df["ts_local"], unit="ms", utc=True)
        df = df.set_index("dt")
    return df
