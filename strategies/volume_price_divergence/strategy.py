"""Volume-price divergence climax reversal on 1h bars.

Thesis: a high-volume trend climax with a large trend-side wick is exhaustion
when the next high-volume bar cannot extend the move. Fade that failed push and
exit at a half-retracement of the prior impulse or the wick extreme.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    "volume_window": 24 * 30,
    "volume_quantile": 0.80,
    "wick_frac": 0.50,
    "trend_lookback": 24,
    "min_trend_move": 0.035,
    "confirm_volume_frac": 0.70,
    "progress_buffer": 0.0015,
    "retracement_frac": 0.50,
    "stop_buffer": 0.0010,
    "max_hold_bars": 24,
    "allow_shorts": 0,
}

PARAM_SPACE: dict = {
    "volume_window": (24 * 14, 24 * 60),
    "volume_quantile": (0.75, 0.95),
    "wick_frac": (0.35, 0.75),
    "trend_lookback": (12, 72),
    "min_trend_move": (0.01, 0.08),
    "confirm_volume_frac": (0.40, 1.10),
    "progress_buffer": (0.0, 0.006),
    "retracement_frac": (0.35, 0.75),
    "stop_buffer": (0.0, 0.004),
    "max_hold_bars": (6, 72),
    "allow_shorts": (0, 1),
}


def _positions_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    open_ = df["open"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    volume_window = int(params.get("volume_window", 24 * 30))
    volume_quantile = float(params.get("volume_quantile", 0.95))
    wick_frac = float(params.get("wick_frac", 0.50))
    trend_lookback = int(params.get("trend_lookback", 24))
    min_trend_move = float(params.get("min_trend_move", 0.025))
    confirm_volume_frac = float(params.get("confirm_volume_frac", 0.70))
    progress_buffer = float(params.get("progress_buffer", 0.0015))
    retracement_frac = float(params.get("retracement_frac", 0.50))
    stop_buffer = float(params.get("stop_buffer", 0.0010))
    max_hold_bars = int(params.get("max_hold_bars", 24))
    allow_shorts = int(params.get("allow_shorts", 0)) == 1

    candle_range = (high - low).replace(0.0, np.nan)
    upper_wick = high - np.maximum(open_, close)
    lower_wick = np.minimum(open_, close) - low
    upper_wick_frac = upper_wick / candle_range
    lower_wick_frac = lower_wick / candle_range

    volume_cutoff = (
        volume.rolling(volume_window, min_periods=volume_window // 2)
        .quantile(volume_quantile)
        .shift(1)
    )
    abnormal_volume = volume >= volume_cutoff

    prior_close = close.shift(trend_lookback)
    trend_move = close / prior_close - 1.0
    prior_low = low.shift(1).rolling(trend_lookback, min_periods=trend_lookback).min()
    prior_high = high.shift(1).rolling(trend_lookback, min_periods=trend_lookback).max()

    up_climax = (
        abnormal_volume
        & (trend_move >= min_trend_move)
        & (upper_wick_frac >= wick_frac)
    )
    down_climax = (
        abnormal_volume
        & (trend_move <= -min_trend_move)
        & (lower_wick_frac >= wick_frac)
    )

    prev_up_climax = up_climax.shift(1).fillna(False)
    prev_down_climax = down_climax.shift(1).fillna(False)
    prev_volume = volume.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close_for_target = close.shift(1)
    prev_prior_low = prior_low.shift(1)
    prev_prior_high = prior_high.shift(1)

    confirm_volume = volume >= confirm_volume_frac * prev_volume
    failed_up = high <= prev_high * (1.0 + progress_buffer)
    failed_down = low >= prev_low * (1.0 - progress_buffer)
    short_signal = prev_up_climax & confirm_volume & failed_up
    long_signal = prev_down_climax & confirm_volume & failed_down

    long_target_series = prev_close_for_target + retracement_frac * (
        prev_prior_high - prev_close_for_target
    )
    short_target_series = prev_close_for_target - retracement_frac * (
        prev_close_for_target - prev_prior_low
    )
    long_stop_series = prev_low * (1.0 - stop_buffer)
    short_stop_series = prev_high * (1.0 + stop_buffer)

    highs = high.to_numpy()
    lows = low.to_numpy()
    closes = close.to_numpy()
    long_sig = long_signal.fillna(False).to_numpy()
    short_sig = short_signal.fillna(False).to_numpy()
    long_targets = long_target_series.to_numpy()
    short_targets = short_target_series.to_numpy()
    long_stops = long_stop_series.to_numpy()
    short_stops = short_stop_series.to_numpy()

    out = np.zeros(len(df), dtype=float)
    state = 0.0
    stop = np.nan
    target = np.nan
    bars_held = 0

    for i in range(len(df)):
        if state == 0.0:
            if long_sig[i] and np.isfinite(long_targets[i]) and long_targets[i] > closes[i]:
                state = 1.0
                stop = long_stops[i]
                target = long_targets[i]
                bars_held = 0
            elif (
                allow_shorts
                and short_sig[i]
                and np.isfinite(short_targets[i])
                and short_targets[i] < closes[i]
            ):
                state = -1.0
                stop = short_stops[i]
                target = short_targets[i]
                bars_held = 0
        else:
            bars_held += 1
            if state > 0:
                exit_trade = lows[i] <= stop or highs[i] >= target
            else:
                exit_trade = highs[i] >= stop or lows[i] <= target

            if exit_trade or bars_held >= max_hold_bars:
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
