"""Baseline volume-weighted 4h Donchian breakout.

Thesis: support/resistance breaks are more likely to be real when the breakout
bar carries abnormal classic candle volume. Enter on a 4h close beyond the
prior local high/low only when breakout-bar volume is at least 3x its 20-bar
moving average. Exit with a fixed ATR take-profit or a stop at the breakout
bar midpoint.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "breakout_lookback": 20,
    "volume_ma": 20,
    "volume_mult": 3.0,
    "atr_period": 14,
    "take_atr": 2.5,
    "max_hold_bars": 30,
}

PARAM_SPACE: dict = {
    "breakout_lookback": (10, 80),
    "volume_ma": (10, 60),
    "volume_mult": (1.5, 5.0),
    "atr_period": (7, 40),
    "take_atr": (1.5, 4.0),
    "max_hold_bars": (8, 72),
}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _positions_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    breakout_lookback = int(params.get("breakout_lookback", 20))
    volume_ma_len = int(params.get("volume_ma", 20))
    volume_mult = float(params.get("volume_mult", 3.0))
    atr_period = int(params.get("atr_period", 14))
    take_atr = float(params.get("take_atr", 2.5))
    max_hold_bars = int(params.get("max_hold_bars", 30))

    prior_high = high.rolling(breakout_lookback, min_periods=breakout_lookback).max().shift(1)
    prior_low = low.rolling(breakout_lookback, min_periods=breakout_lookback).min().shift(1)
    avg_volume = volume.rolling(volume_ma_len, min_periods=volume_ma_len).mean().shift(1)
    atr = _atr(df, atr_period)

    abnormal_volume = volume >= volume_mult * avg_volume
    long_entry = (close > prior_high) & abnormal_volume
    short_entry = (close < prior_low) & abnormal_volume

    highs = high.to_numpy()
    lows = low.to_numpy()
    closes = close.to_numpy()
    atrs = atr.to_numpy()
    mids = ((high + low) / 2.0).to_numpy()
    long_sig = long_entry.fillna(False).to_numpy()
    short_sig = short_entry.fillna(False).to_numpy()

    out = np.zeros(len(df), dtype=float)
    state = 0.0
    stop = np.nan
    target = np.nan
    bars_held = 0

    for i in range(len(df)):
        c = closes[i]
        a = atrs[i]
        if not np.isfinite(c) or not np.isfinite(a):
            out[i] = state
            continue

        if state == 0.0:
            if long_sig[i]:
                state = 1.0
                stop = mids[i]
                target = c + take_atr * a
                bars_held = 0
            elif short_sig[i]:
                state = -1.0
                stop = mids[i]
                target = c - take_atr * a
                bars_held = 0
        else:
            bars_held += 1
            if state > 0:
                bracket_done = lows[i] <= stop or highs[i] >= target
            else:
                bracket_done = highs[i] >= stop or lows[i] <= target
            if bracket_done or bars_held >= max_hold_bars:
                state = 0.0
                stop = np.nan
                target = np.nan
                bars_held = 0

        out[i] = state

    return pd.Series(out, index=df.index).shift(1).fillna(0.0)


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _positions_for_symbol(df, params)
        frames.append(
            pd.DataFrame(
                {"timestamp": df.index, "symbol": symbol, "position": pos.values}
            )
        )
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
