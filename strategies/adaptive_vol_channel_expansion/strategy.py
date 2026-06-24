"""Adaptive Volatility Channel Expansion.

Hypothesis: Bollinger-width compression on liquid crypto perps often precedes
a directional volatility expansion. Enter on the first breakout through the
prior channel boundary after a squeeze; exit on the opposite Keltner boundary
or when band width reaches an extreme expansion regime.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = ["SOLUSDT", "DOGEUSDT", "AVAXUSDT"]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    "bb_period": 20,
    "bb_std": 2.0,
    "squeeze_lookback": 240,
    "squeeze_pct": 0.15,
    "expand_pct": 0.85,
    "atr_period": 20,
    "keltner_k": 1.5,
    "trend_slope_lb": 48,
    "min_trend_atr": 1.0,
    "long_size": 0.25,
    "max_hold": 96,
}

PARAM_SPACE: dict = {
    "bb_period": (12, 48),
    "bb_std": (1.5, 2.8),
    "squeeze_lookback": (120, 720),
    "squeeze_pct": (0.05, 0.30),
    "expand_pct": (0.70, 0.95),
    "atr_period": (10, 48),
    "keltner_k": (1.0, 3.0),
    "trend_slope_lb": (24, 120),
    "min_trend_atr": (0.25, 2.0),
    "long_size": (0.25, 1.0),
    "max_hold": (24, 168),
}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _rolling_percentile_rank(series: pd.Series, window: int) -> pd.Series:
    def rank_last(values: np.ndarray) -> float:
        last = values[-1]
        if np.isnan(last):
            return np.nan
        valid = values[~np.isnan(values)]
        if len(valid) == 0:
            return np.nan
        return float((valid <= last).sum() / len(valid))

    return series.rolling(window, min_periods=window).apply(rank_last, raw=True)


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"]
    bb_period = int(params.get("bb_period", 20))
    bb_std = float(params.get("bb_std", 2.0))
    squeeze_lookback = int(params.get("squeeze_lookback", 240))
    squeeze_pct = float(params.get("squeeze_pct", 0.15))
    expand_pct = float(params.get("expand_pct", 0.85))
    atr_period = int(params.get("atr_period", 20))
    keltner_k = float(params.get("keltner_k", 1.5))
    trend_slope_lb = int(params.get("trend_slope_lb", 48))
    min_trend_atr = float(params.get("min_trend_atr", 1.0))
    long_size = float(params.get("long_size", 0.5))
    max_hold = int(params.get("max_hold", 96))

    bb_mid = close.rolling(bb_period, min_periods=bb_period).mean()
    bb_sd = close.rolling(bb_period, min_periods=bb_period).std(ddof=0)
    bb_upper = bb_mid + bb_std * bb_sd
    bb_lower = bb_mid - bb_std * bb_sd
    bb_width = ((bb_upper - bb_lower) / bb_mid).replace([np.inf, -np.inf], np.nan)
    width_rank = _rolling_percentile_rank(bb_width, squeeze_lookback)

    atr = _atr(df, atr_period)
    keltner_mid = close.ewm(span=bb_period, adjust=False).mean()
    keltner_upper = keltner_mid + keltner_k * atr
    keltner_lower = keltner_mid - keltner_k * atr
    trend_slope_atr = (keltner_mid - keltner_mid.shift(trend_slope_lb)) / atr
    trend_ok = trend_slope_atr.abs() >= min_trend_atr

    squeeze = width_rank <= squeeze_pct
    extreme_expand = width_rank >= expand_pct
    armed = squeeze.shift(1).fillna(False) & trend_ok.shift(1).fillna(False)
    long_break = (close > bb_upper.shift(1)) & armed
    short_break = (close < bb_lower.shift(1)) & armed

    pos = np.zeros(len(df))
    state = 0.0
    bars_held = 0

    closes = close.to_numpy()
    k_upper = keltner_upper.to_numpy()
    k_lower = keltner_lower.to_numpy()
    le = long_break.to_numpy()
    se = short_break.to_numpy()
    x_expand = extreme_expand.to_numpy()

    for i in range(len(df)):
        if state == 0.0:
            bars_held = 0
            if le[i]:
                state = long_size
                bars_held = 1
            elif se[i]:
                state = -1.0
                bars_held = 1
        else:
            bars_held += 1
            if state > 0:
                exit_now = closes[i] < k_lower[i] or x_expand[i] or bars_held >= max_hold
            else:
                exit_now = closes[i] > k_upper[i] or x_expand[i] or bars_held >= max_hold
            if exit_now:
                state = 0.0
                bars_held = 0

        pos[i] = state

    return pd.Series(pos, index=df.index).shift(1).fillna(0.0)


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
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
