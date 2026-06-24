"""Session High/Low False Breakout on 5-minute bars.

Fade London-open sweeps of the 00:00-08:00 UTC Asian range when the bar closes
back inside the range.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "5min"

DEFAULT_PARAMS: dict = {
    "entry_start_hour": 8,
    "entry_end_hour": 10,
    "asian_start_hour": 0,
    "asian_end_hour": 8,
    "target": 1.0,  # 0.5 = range midpoint, 1.0 = opposite Asian boundary
    "stop_buffer": 0.10,  # fraction of Asian range beyond swept edge
    "min_range_pct": 0.002,
    "max_range_pct": 0.06,
    "max_hold_bars": 72,  # six hours on 5-minute bars
    "allow_shorts": 0,
    "trend_fast_4h": 12,
    "trend_slow_4h": 48,
}

PARAM_SPACE: dict = {
    "target": (0.4, 1.0),
    "stop_buffer": (0.02, 0.25),
    "min_range_pct": (0.001, 0.008),
    "max_range_pct": (0.02, 0.10),
    "max_hold_bars": (24, 144),
}


def _session_positions(df: pd.DataFrame, params: dict) -> pd.Series:
    entry_start = int(params.get("entry_start_hour", 8))
    entry_end = int(params.get("entry_end_hour", 10))
    asian_start = int(params.get("asian_start_hour", 0))
    asian_end = int(params.get("asian_end_hour", 8))
    target_frac = float(params.get("target", 0.5))
    stop_buffer = float(params.get("stop_buffer", 0.10))
    min_range_pct = float(params.get("min_range_pct", 0.002))
    max_range_pct = float(params.get("max_range_pct", 0.06))
    max_hold_bars = int(params.get("max_hold_bars", 72))
    allow_shorts = int(params.get("allow_shorts", 0)) == 1
    trend_fast_4h = int(params.get("trend_fast_4h", 12))
    trend_slow_4h = int(params.get("trend_slow_4h", 48))

    df4h = resample_higher(df, "4h", {"close": "last"}, target_index=df.index)
    ema_fast = df4h["close"].ewm(
        span=trend_fast_4h, adjust=False, min_periods=trend_fast_4h
    ).mean()
    ema_slow = df4h["close"].ewm(
        span=trend_slow_4h, adjust=False, min_periods=trend_slow_4h
    ).mean()
    bull_trend = (ema_fast > ema_slow).fillna(False).to_numpy(dtype=bool)

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    index = df.index

    pos = np.zeros(len(df), dtype=float)
    cur = 0.0
    stop = np.nan
    target = np.nan
    bars_held = 0

    day_key = None
    asian_high = np.nan
    asian_low = np.nan
    session_ready = False
    traded_today = False

    for i, ts in enumerate(index):
        current_day = ts.date()
        if current_day != day_key:
            day_key = current_day
            asian_high = np.nan
            asian_low = np.nan
            session_ready = False
            traded_today = False

        hour_float = ts.hour + ts.minute / 60.0
        in_asia = asian_start <= hour_float < asian_end
        in_entry = entry_start <= hour_float < entry_end

        if in_asia:
            asian_high = high[i] if np.isnan(asian_high) else max(asian_high, high[i])
            asian_low = low[i] if np.isnan(asian_low) else min(asian_low, low[i])
            pos[i] = cur
            continue

        if not session_ready and hour_float >= asian_end:
            session_ready = np.isfinite(asian_high) and np.isfinite(asian_low)

        if cur != 0.0:
            bars_held += 1
            if cur > 0:
                if close[i] >= target or close[i] <= stop or bars_held >= max_hold_bars:
                    cur = 0.0
            else:
                if close[i] <= target or close[i] >= stop or bars_held >= max_hold_bars:
                    cur = 0.0

        if cur == 0.0 and session_ready and in_entry and not traded_today:
            range_abs = asian_high - asian_low
            range_pct = range_abs / close[i] if close[i] > 0 else np.nan
            valid_range = (
                np.isfinite(range_abs)
                and range_abs > 0
                and min_range_pct <= range_pct <= max_range_pct
            )

            if valid_range:
                mid = asian_low + target_frac * range_abs
                swept_high = high[i] > asian_high and close[i] < asian_high
                swept_low = low[i] < asian_low and close[i] > asian_low

                if swept_low and bull_trend[i]:
                    cur = 1.0
                    stop = asian_low - stop_buffer * range_abs
                    target = mid
                    bars_held = 0
                    traded_today = True
                elif allow_shorts and swept_high:
                    cur = -1.0
                    stop = asian_high + stop_buffer * range_abs
                    target = asian_high - target_frac * range_abs
                    bars_held = 0
                    traded_today = True

        pos[i] = cur

    return pd.Series(pos, index=index).shift(1).fillna(0.0)


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        position = _session_positions(df, params)
        frames.append(
            pd.DataFrame(
                {
                    "timestamp": df.index,
                    "symbol": symbol,
                    "position": position.values,
                }
            )
        )

    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
