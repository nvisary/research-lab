"""VWAP z-score dip-buy long-only in confirmed uptrend.

Hypothesis: When asset is in confirmed uptrend (close > EMA200 with positive
slope), deviations below rolling VWAP (z < -1.5) are buyable dips that revert
back to VWAP. Long-only, multi-symbol — captures pullback-buy in 2024-2026
majors rally.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    "vwap_window": 48,
    "z_entry": 1.5,
    "z_exit": -0.1,
    "trend_span": 200,
    "slope_lb": 48,
    "max_hold_bars": 48,
}

PARAM_SPACE: dict = {
    "vwap_window": (24, 120),
    "z_entry": (1.0, 3.0),
    "z_exit": (-0.5, 0.5),
    "trend_span": (100, 400),
    "slope_lb": (12, 96),
    "max_hold_bars": (12, 96),
}


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"]
    vol = df["volume"]
    tp = (df["high"] + df["low"] + df["close"]) / 3.0

    win = int(params.get("vwap_window", 48))
    z_entry = float(params.get("z_entry", 1.5))
    z_exit = float(params.get("z_exit", -0.1))
    trend_span = int(params.get("trend_span", 200))
    slope_lb = int(params.get("slope_lb", 48))
    max_hold = int(params.get("max_hold_bars", 48))

    pv = (tp * vol).rolling(win).sum()
    vv = vol.rolling(win).sum()
    vwap = pv / vv.replace(0, np.nan)
    dev = close - vwap
    sd = dev.rolling(win).std(ddof=0)
    z = dev / sd.replace(0, np.nan)

    trend = close.ewm(span=trend_span, adjust=False).mean()
    slope = trend - trend.shift(slope_lb)
    bull = (close > trend) & (slope > 0)

    z_v = z.values
    bull_v = bull.values

    pos = np.zeros(len(df))
    state = 0
    bars_held = 0
    for i in range(len(df)):
        zi = z_v[i]
        if np.isnan(zi):
            pos[i] = state
            continue
        if state == 0:
            if bull_v[i] and zi < -z_entry:
                state = 1
                bars_held = 0
        elif state == 1:
            bars_held += 1
            # Exit: z reverts past exit threshold, time stop, or trend flip
            if zi > z_exit or bars_held >= max_hold or (not bull_v[i]):
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
