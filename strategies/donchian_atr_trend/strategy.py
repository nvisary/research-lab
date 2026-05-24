"""Donchian fade in chop (counter-trend reversion to channel mid).

Hypothesis: When the market is non-trending, donchian-N highs/lows are likely
exhaustion points rather than breakouts. Fade them and revert to channel mid.
Chop-only gate (same pattern that fixed vwap/bb) is the key — without it, fading
breakouts in real trends is suicide.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT"]
DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "donchian_n": 20,
    "trend_span": 100,
    "slope_lb": 12,
    "max_slope_pct": 0.05,
    "atr_period": 14,
    "atr_stop_k": 2.5,
    "max_hold_bars": 24,
}

PARAM_SPACE: dict = {
    "donchian_n": (10, 60),
    "trend_span": (50, 300),
    "slope_lb": (6, 36),
    "max_slope_pct": (0.01, 0.10),
    "atr_period": (7, 28),
    "atr_stop_k": (1.5, 5.0),
    "max_hold_bars": (8, 60),
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
    high = df["high"]
    low = df["low"]
    n = int(params.get("donchian_n", 20))
    trend_span = int(params.get("trend_span", 100))
    slope_lb = int(params.get("slope_lb", 12))
    max_slope_pct = float(params.get("max_slope_pct", 0.05))
    atr_period = int(params.get("atr_period", 14))
    atr_stop_k = float(params.get("atr_stop_k", 2.5))
    max_hold = int(params.get("max_hold_bars", 24))

    upper = high.rolling(n).max()
    lower = low.rolling(n).min()
    mid = (upper + lower) / 2.0

    # Chop filter on daily
    df_d = resample_higher(df, "1D", {"close": "last"}, target_index=df.index)
    trend = df_d["close"].ewm(span=trend_span, adjust=False).mean()
    slope_pct = (trend - trend.shift(slope_lb)) / trend
    chop = slope_pct.abs() < max_slope_pct

    atr = _atr(df, atr_period)

    # Fade: prior-bar pierce of donchian high → short; prior-bar pierce of low → long
    fade_short = (high.shift(1) >= upper.shift(2)) & (close < upper.shift(1))
    fade_long = (low.shift(1) <= lower.shift(2)) & (close > lower.shift(1))

    fl = fade_long.values
    fs = fade_short.values
    chop_v = chop.values
    closes = close.values
    mids = mid.values
    atrs = atr.values

    pos = np.zeros(len(df))
    state = 0
    bars_held = 0
    entry_price = 0.0
    for i in range(len(df)):
        if state == 0:
            if chop_v[i] and fl[i]:
                state = 1
                bars_held = 0
                entry_price = closes[i]
            elif chop_v[i] and fs[i]:
                state = -1
                bars_held = 0
                entry_price = closes[i]
        elif state == 1:
            bars_held += 1
            stop = entry_price - atr_stop_k * atrs[i]
            if closes[i] >= mids[i] or closes[i] <= stop or bars_held >= max_hold:
                state = 0
                bars_held = 0
                entry_price = 0.0
        elif state == -1:
            bars_held += 1
            stop = entry_price + atr_stop_k * atrs[i]
            if closes[i] <= mids[i] or closes[i] >= stop or bars_held >= max_hold:
                state = 0
                bars_held = 0
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
