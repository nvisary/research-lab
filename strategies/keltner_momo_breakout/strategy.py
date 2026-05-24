"""Keltner channel upper-break long-only momentum, trend-aligned.

Hypothesis: Long entries on Keltner upper-band breaks in a confirmed uptrend
(close > EMA200) ride trend continuation. ATR stop + EMA cross-down exit
lets winners run while cutting losers fast. Multi-symbol on majors.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "ema_period": 20,
    "atr_period": 20,
    "kelt_k": 1.5,
    "trend_span": 100,
    "atr_stop_k": 3.0,
}

PARAM_SPACE: dict = {
    "ema_period": (10, 50),
    "atr_period": (10, 50),
    "kelt_k": (1.0, 3.5),
    "trend_span": (50, 300),
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
    ema_period = int(params.get("ema_period", 20))
    atr_period = int(params.get("atr_period", 20))
    kelt_k = float(params.get("kelt_k", 1.5))
    trend_span = int(params.get("trend_span", 100))
    atr_stop_k = float(params.get("atr_stop_k", 3.0))

    ema = close.ewm(span=ema_period, adjust=False).mean()
    atr = _atr(df, atr_period)
    upper = ema + kelt_k * atr

    trend = close.ewm(span=trend_span, adjust=False).mean()
    bull = close > trend

    long_entry = (close > upper.shift(1)) & bull

    le = long_entry.values
    bull_v = bull.values
    ema_v = ema.values
    closes = close.values
    atrs = atr.values

    pos = np.zeros(len(df))
    state = 0
    entry_price = 0.0
    for i in range(len(df)):
        if state == 0:
            if le[i]:
                state = 1
                entry_price = closes[i]
        elif state == 1:
            stop = entry_price - atr_stop_k * atrs[i]
            if closes[i] <= stop or closes[i] < ema_v[i] or (not bull_v[i]):
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
