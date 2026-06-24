"""Keltner pierce fade in low-trend BTC regimes."""

from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT"]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    "ema_period": 20,
    "atr_period": 20,
    "kelt_k": 2.5,
    "trend_span": 100,
    "slope_lookback": 3,
    "max_slope_abs": 0.01,
    "atr_stop_k": 3.0,
    "timeout_bars": 24,
}

PARAM_SPACE: dict = {
    "ema_period": (10, 50),
    "atr_period": (10, 50),
    "kelt_k": (1.5, 3.5),
    "trend_span": (50, 200),
    "slope_lookback": (6, 24),
    "max_slope_abs": (0.005, 0.05),
    "atr_stop_k": (1.5, 4.0),
    "timeout_bars": (8, 48),
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

    ema_period = int(params.get("ema_period", 20))
    atr_period = int(params.get("atr_period", 20))
    kelt_k = float(params.get("kelt_k", 2.5))
    trend_span = int(params.get("trend_span", 100))
    slope_lookback = int(params.get("slope_lookback", 12))
    max_slope_abs = float(params.get("max_slope_abs", 0.02))
    atr_stop_k = float(params.get("atr_stop_k", 2.5))
    timeout_bars = int(params.get("timeout_bars", 24))

    ema = close.ewm(span=ema_period, adjust=False).mean()
    atr = _atr(df, atr_period)
    upper = ema + kelt_k * atr
    lower = ema - kelt_k * atr

    df4h = resample_higher(df, "4h", {"close": "last"}, target_index=df.index)
    trend = df4h["close"].ewm(span=trend_span, adjust=False).mean()
    slope = trend.pct_change(slope_lookback).abs()
    chop = slope < max_slope_abs

    long_entry = (low < lower) & (close > lower) & chop
    short_entry = (high > upper) & (close < upper) & chop

    le = long_entry.values
    se = short_entry.values
    ema_v = ema.values
    closes = close.values
    atrs = atr.values

    pos = np.zeros(len(df))
    state = 0
    entry_price = 0.0
    bars_held = 0
    for i in range(len(df)):
        if state == 0:
            bars_held = 0
            if le[i]:
                state = 1
                entry_price = closes[i]
            elif False and se[i]:
                state = -1
                entry_price = closes[i]
        elif state == 1:
            bars_held += 1
            stop = entry_price - atr_stop_k * atrs[i]
            if closes[i] <= stop or closes[i] >= ema_v[i] or bars_held >= timeout_bars:
                state = 0
                entry_price = 0.0
        elif state == -1:
            bars_held += 1
            stop = entry_price + atr_stop_k * atrs[i]
            if closes[i] >= stop or closes[i] <= ema_v[i] or bars_held >= timeout_bars:
                state = 0
                entry_price = 0.0
        pos[i] = state

    return pd.Series(pos, index=df.index).shift(1).fillna(0.0)


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
