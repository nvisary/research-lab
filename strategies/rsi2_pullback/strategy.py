"""RSI(2) pullback long-only buy-the-dip in confirmed uptrend.

Hypothesis: Classic Connors RSI(2) pullback: when an asset is in a confirmed
uptrend (close > EMA200), short-term oversold (RSI2 < 10) marks high-prob
mean-reversion entries. Exit on RSI back to neutral or trend break.
Long-only, multi-symbol — rides 2024-2026 majors rally.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "rsi_period": 2,
    "rsi_low": 15,
    "rsi_exit": 65,
    "trend_span": 200,
    "max_hold_bars": 20,
}

PARAM_SPACE: dict = {
    "rsi_period": (2, 7),
    "rsi_low": (5, 25),
    "rsi_exit": (50, 80),
    "trend_span": (100, 400),
    "max_hold_bars": (8, 60),
}


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    al = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"]
    rsi_period = int(params.get("rsi_period", 2))
    rsi_low = float(params.get("rsi_low", 15))
    rsi_exit = float(params.get("rsi_exit", 65))
    trend_span = int(params.get("trend_span", 200))
    max_hold = int(params.get("max_hold_bars", 20))

    rsi = _rsi(close, rsi_period)
    trend = close.ewm(span=trend_span, adjust=False).mean()
    bull = close > trend

    rsi_v = rsi.values
    bull_v = bull.values

    pos = np.zeros(len(df))
    state = 0
    bars_held = 0
    for i in range(len(df)):
        if state == 0:
            if bull_v[i] and rsi_v[i] < rsi_low:
                state = 1
                bars_held = 0
        elif state == 1:
            bars_held += 1
            if rsi_v[i] > rsi_exit or bars_held >= max_hold or (not bull_v[i]):
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
