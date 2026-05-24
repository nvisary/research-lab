"""Bollinger Band upper-break momentum long, trend-aligned.

Hypothesis: Going long on Bollinger Band upper-band breakouts only when the
asset is in a confirmed uptrend (close > EMA200 with positive slope) captures
trend onset across the 2024-2026 bull regime. Exit on close back below BB
mid or trend flip — let winners run.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "bb_period": 20,
    "bb_std": 2.0,
    "trend_span": 100,
    "slope_lb": 24,
    "atr_period": 14,
    "atr_stop_k": 3.0,
}

PARAM_SPACE: dict = {
    "bb_period": (10, 50),
    "bb_std": (1.5, 3.0),
    "trend_span": (50, 300),
    "slope_lb": (6, 48),
    "atr_period": (7, 28),
    "atr_stop_k": (1.5, 5.0),
}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"]
    bb_period = int(params.get("bb_period", 20))
    bb_std = float(params.get("bb_std", 2.0))
    trend_span = int(params.get("trend_span", 100))
    slope_lb = int(params.get("slope_lb", 24))
    atr_period = int(params.get("atr_period", 14))
    atr_stop_k = float(params.get("atr_stop_k", 3.0))

    mid = close.rolling(bb_period).mean()
    sd = close.rolling(bb_period).std(ddof=0)
    upper = mid + bb_std * sd

    trend = close.ewm(span=trend_span, adjust=False).mean()
    slope = trend - trend.shift(slope_lb)
    bull = (close > trend) & (slope > 0)

    atr = _atr(df, atr_period)

    # Long entry: close breaks above prior upper band AND in uptrend
    long_entry = (close > upper.shift(1)) & bull

    pos = np.zeros(len(df))
    state = 0
    entry_price = 0.0
    closes = close.values
    mids = mid.values
    atrs = atr.values
    le = long_entry.values
    bull_v = bull.values

    for i in range(len(df)):
        if state == 0:
            if le[i]:
                state = 1
                entry_price = closes[i]
        elif state == 1:
            stop = entry_price - atr_stop_k * atrs[i]
            # Exit: ATR stop hit, close back below BB mid, or trend flip
            if closes[i] <= stop or closes[i] < mids[i] or (not bull_v[i]):
                state = 0
                entry_price = 0.0
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
