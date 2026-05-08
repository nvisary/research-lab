"""Universe filters: pick symbols by liquidity / history coverage."""
from __future__ import annotations

import pandas as pd

from .loader import available_symbols, load


def top_by_volume(start: str, end: str, n: int = 30, tf: str = "1h") -> list[str]:
    """Rank locally-available symbols by total quote volume (close * volume) in [start, end)."""
    scored: list[tuple[str, float]] = []
    for sym in available_symbols():
        df = load(sym, start, end, tf=tf)
        if df.empty:
            continue
        scored.append((sym, float((df["close"] * df["volume"]).sum())))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in scored[:n]]


def alive_during(start: str, end: str, min_coverage: float = 0.95) -> list[str]:
    """Symbols whose 1m bars cover at least `min_coverage` fraction of the period."""
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    expected = int((end_ts - start_ts).total_seconds() // 60)
    out = []
    for sym in available_symbols():
        df = load(sym, start, end, tf="1min")
        if len(df) >= expected * min_coverage:
            out.append(sym)
    return out
