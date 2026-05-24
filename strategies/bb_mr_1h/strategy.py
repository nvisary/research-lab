"""RSI(2) pullback -> Bollinger-band MR on 1h.

Iter 9: abandon RSI(2) mechanic entirely. Switch to canonical
Bollinger-band mean reversion: long when close < lower band (z<-2),
exit when close >= midline. Short when close > upper, exit at mid.
Long-side bias by close>EMA200, short-side by close<EMA200 (long-only-
in-uptrend MR convention from Connors+Bollinger).

Same multi-symbol set, 1h TF.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    "bb_period": 20,
    "bb_k": 2.5,
    "trend_span": 200,
    "max_hold_bars": 48,
}

PARAM_SPACE: dict = {
    "bb_period": (10, 60),
    "bb_k": (1.5, 3.5),
    "trend_span": (100, 400),
    "max_hold_bars": (12, 120),
}


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"]
    bb_period = int(params.get("bb_period", 20))
    bb_k = float(params.get("bb_k", 2.0))
    trend_span = int(params.get("trend_span", 200))
    max_hold = int(params.get("max_hold_bars", 48))

    mid = close.rolling(bb_period).mean()
    sd = close.rolling(bb_period).std(ddof=0)
    upper = mid + bb_k * sd
    lower = mid - bb_k * sd
    trend = close.ewm(span=trend_span, adjust=False).mean()

    c = close.values
    mid_v = mid.values
    up_v = upper.values
    lo_v = lower.values
    bull = (close > trend).fillna(False).values
    bear = (close < trend).fillna(False).values

    n = len(df)
    pos = np.zeros(n)
    state = 0
    bars_held = 0
    for i in range(n):
        if np.isnan(mid_v[i]):
            pos[i] = state
            continue
        if state == 0:
            if bull[i] and c[i] < lo_v[i]:
                state = 1
                bars_held = 0
            elif bear[i] and c[i] > up_v[i]:
                state = -1
                bars_held = 0
        elif state == 1:
            bars_held += 1
            if c[i] >= mid_v[i] or bars_held >= max_hold:
                state = 0
                bars_held = 0
        elif state == -1:
            bars_held += 1
            if c[i] <= mid_v[i] or bars_held >= max_hold:
                state = 0
                bars_held = 0
        pos[i] = state

    pos_s = pd.Series(pos, index=df.index)
    return pos_s.shift(1).fillna(0.0)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _signals_for_symbol(df, params)
        frames.append(
            pd.DataFrame({"timestamp": df.index, "symbol": symbol, "position": pos.values})
        )
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
