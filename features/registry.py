"""Feature registry + parquet cache.

Cache layout mirrors ``data/bybit/perp/1m/``:
  ``data/features/<feature>/<symbol>/<tf>/<YYYY-MM>.parquet``

Each parquet file has one column = feature values, indexed by tz-aware
UTC timestamp. Partitioning by month makes incremental updates cheap
(only the current month is rewritten on extend) and keeps file sizes
sensible.

A feature is registered with:
  - name        (unique key, lowercase + underscore)
  - func        callable (symbol, start, end, tf) -> pd.Series
  - description short human-readable string
  - deps        list of strings: "ohlcv" | "funding" | "<other feature>"
  - lookback    pd.Timedelta — how much history before `start` the
                feature needs to "warm up" (e.g. ATR-14 on 1h bars
                needs ~14h). Cache reads add this padding silently
                so the returned series is "clean" at `start`.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from datafeed.loader import data_root


# --------------------------------------------------------------------------- #
@dataclass
class FeatureSpec:
    name: str
    func: Callable[..., pd.Series]
    description: str
    deps: list[str] = field(default_factory=list)
    lookback: pd.Timedelta = field(default_factory=lambda: pd.Timedelta("30D"))


_REGISTRY: dict[str, FeatureSpec] = {}


def register(name: str, *, description: str = "",
             deps: list[str] | None = None,
             lookback: str | pd.Timedelta = "30D"):
    """Decorator. Usage:

        @register("atr_14", description="14-bar ATR", deps=["ohlcv"], lookback="2D")
        def atr_14(symbol, start, end, tf):
            ...
    """
    lb = lookback if isinstance(lookback, pd.Timedelta) else pd.Timedelta(lookback)

    def _wrap(fn: Callable[..., pd.Series]) -> Callable[..., pd.Series]:
        if name in _REGISTRY:
            raise ValueError(f"feature {name!r} already registered")
        _REGISTRY[name] = FeatureSpec(
            name=name, func=fn, description=description,
            deps=list(deps or []), lookback=lb,
        )
        return fn

    return _wrap


def list_features() -> list[str]:
    return sorted(_REGISTRY.keys())


def feature_meta(name: str) -> dict:
    if name not in _REGISTRY:
        raise KeyError(f"unknown feature: {name}")
    s = _REGISTRY[name]
    return {
        "name": s.name,
        "description": s.description,
        "deps": list(s.deps),
        "lookback": str(s.lookback),
        "source_file": inspect.getsourcefile(s.func) or "",
        "source_line": (inspect.getsourcelines(s.func)[1]
                        if inspect.getsourcefile(s.func) else None),
    }


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def _cache_root() -> Path:
    return data_root() / "features"


def _cache_path(feature: str, symbol: str, tf: str,
                year: int, month: int) -> Path:
    return _cache_root() / feature / symbol / tf / f"{year:04d}-{month:02d}.parquet"


def _months_between(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[int, int]]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def _read_cache(feature: str, symbol: str, tf: str,
                start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Read whatever the cache has for [start, end). May return a partial
    series; the caller decides whether to fill gaps by computing.
    """
    parts = []
    for y, m in _months_between(start, end):
        p = _cache_path(feature, symbol, tf, y, m)
        if p.exists():
            parts.append(pd.read_parquet(p))
    if not parts:
        return pd.Series(dtype="float64",
                         index=pd.DatetimeIndex([], tz="UTC", name="timestamp"),
                         name=feature)
    df = pd.concat(parts).sort_index()
    df = df[(df.index >= start) & (df.index < end)]
    if feature not in df.columns:
        # Tolerate the case where the parquet stored a single column under
        # a different name (e.g. value series with auto-named column).
        col = df.columns[0]
        s = df[col].rename(feature)
    else:
        s = df[feature]
    return s


def _write_cache(feature: str, symbol: str, tf: str, series: pd.Series) -> None:
    if series.empty:
        return
    by_month: dict[tuple[int, int], pd.Series] = {}
    for ts, val in series.items():
        key = (ts.year, ts.month)
        by_month.setdefault(key, []).append((ts, val))
    for (y, m), pairs in by_month.items():
        idx = pd.DatetimeIndex([t for t, _ in pairs], tz="UTC", name="timestamp")
        vals = [v for _, v in pairs]
        df = pd.DataFrame({feature: vals}, index=idx)
        p = _cache_path(feature, symbol, tf, y, m)
        p.parent.mkdir(parents=True, exist_ok=True)
        # If a partial cache exists for this month, merge (cache is
        # idempotent — feature is a deterministic function of inputs).
        if p.exists():
            existing = pd.read_parquet(p)
            df = pd.concat([existing, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        df.to_parquet(p, compression="zstd")


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def compute(name: str, symbol: str,
            start: str | pd.Timestamp, end: str | pd.Timestamp,
            tf: str = "1h",
            use_cache: bool = True) -> pd.Series:
    """Compute (or load from cache) feature ``name`` for ``symbol`` over
    [start, end) at timeframe ``tf``.

    Returns a Series indexed by tz-aware UTC timestamps, named ``name``.
    Empty if the underlying data is missing.

    Caching strategy:
      1. Read whatever's already in cache for the period.
      2. If cache misses any bars (vs. expected OHLCV bars from loader),
         compute the missing range with full lookback padding and store.
      3. Return the union.

    Set ``use_cache=False`` to force recomputation (useful during
    feature-development).
    """
    if name not in _REGISTRY:
        raise KeyError(f"unknown feature: {name!r} (have: {list_features()})")
    spec = _REGISTRY[name]

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    start = start.tz_convert("UTC") if start.tzinfo else start.tz_localize("UTC")
    end = end.tz_convert("UTC") if end.tzinfo else end.tz_localize("UTC")

    if use_cache:
        cached = _read_cache(name, symbol, tf, start, end)
        if not cached.empty:
            # Heuristic: if the cached range fully spans [start, end), trust it.
            # We don't try to detect partial gaps within the range — that's
            # rare in practice and a recompute is cheap.
            if cached.index.min() <= start + spec.lookback / 4 and \
               cached.index.max() >= end - pd.Timedelta("1D"):
                return cached.loc[(cached.index >= start) & (cached.index < end)]

    # Cache miss — compute. Add lookback padding so rolling windows warm up.
    compute_start = start - spec.lookback
    series = spec.func(symbol=symbol, start=compute_start, end=end, tf=tf)
    if series is None or len(series) == 0:
        return pd.Series(dtype="float64",
                         index=pd.DatetimeIndex([], tz="UTC", name="timestamp"),
                         name=name)
    series = series.rename(name)
    series = series.loc[(series.index >= start) & (series.index < end)]

    if use_cache:
        _write_cache(name, symbol, tf, series)
    return series


def compute_many(names: list[str], symbol: str,
                 start: str | pd.Timestamp, end: str | pd.Timestamp,
                 tf: str = "1h",
                 use_cache: bool = True) -> pd.DataFrame:
    """Compute several features for one symbol; return them as columns
    of a single DataFrame, aligned on a UNION of timestamps. Missing
    cells are NaN (caller decides how to fill / drop)."""
    cols = {n: compute(n, symbol, start, end, tf, use_cache=use_cache) for n in names}
    if not cols:
        return pd.DataFrame()
    df = pd.concat(cols.values(), axis=1)
    df.columns = list(cols.keys())
    return df


# --------------------------------------------------------------------------- #
def coverage_table(name: str) -> list[dict]:
    """For each (symbol, tf) directory on disk under this feature's cache,
    report what months are present. Used by the UI's feature browser."""
    root = _cache_root() / name
    if not root.exists():
        return []
    out = []
    for sym_dir in sorted(root.iterdir()):
        if not sym_dir.is_dir():
            continue
        for tf_dir in sorted(sym_dir.iterdir()):
            if not tf_dir.is_dir():
                continue
            months = sorted(p.stem for p in tf_dir.glob("*.parquet"))
            if months:
                out.append({
                    "symbol": sym_dir.name,
                    "tf": tf_dir.name,
                    "months": months,
                    "n_months": len(months),
                    "first": months[0],
                    "last": months[-1],
                })
    return out


def clear_cache(name: str | None = None) -> int:
    """Delete cached parquets for ``name`` (or ALL features if None).
    Returns number of files removed. Useful when changing a feature's
    implementation — never call from inside generate_signals.
    """
    import shutil
    root = _cache_root()
    if not root.exists():
        return 0
    if name is None:
        count = sum(1 for _ in root.rglob("*.parquet"))
        shutil.rmtree(root)
        return count
    target = root / name
    if not target.exists():
        return 0
    count = sum(1 for _ in target.rglob("*.parquet"))
    shutil.rmtree(target)
    return count
