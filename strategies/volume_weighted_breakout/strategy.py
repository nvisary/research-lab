"""Volume-weighted 4h Donchian breakout.

Thesis: support/resistance breaks are more likely to continue when the
breakout bar carries abnormal volume, suggesting real capital flow rather than
a thin stop run. Entries require a 4h close beyond the prior local high/low and
volume at least a multiple of its 20-bar moving average.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]
DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "breakout_lookback": 8,
    "volume_ma": 20,
    "volume_mult": 1.9,
    "flat_trend_span": 60,
    "flat_slope_bars": 12,
    "flat_slope_abs": 0.005,
    "atr_period": 14,
    "exhaustion_bars": 6,
    "max_prior_move": 0.08,
    "follow_volume_mult": 1.2,
    "follow_bars": 3,
    "short_cluster_min": 3,
    "short_cluster_prior_move": -0.045,
    "short_cluster_core": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"],
    "take_atr": 2.5,
    "max_hold_bars": 30,
}

PARAM_SPACE: dict = {
    "breakout_lookback": (10, 80),
    "volume_ma": (10, 60),
    "volume_mult": (1.5, 5.0),
    "flat_trend_span": (30, 180),
    "flat_slope_bars": (6, 36),
    "flat_slope_abs": (0.002, 0.05),
    "atr_period": (7, 40),
    "exhaustion_bars": (3, 12),
    "max_prior_move": (0.04, 0.15),
    "follow_volume_mult": (1.0, 1.8),
    "follow_bars": (2, 8),
    "short_cluster_min": (2, 4),
    "short_cluster_prior_move": (-0.08, -0.03),
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
    flat_trend_span = int(params.get("flat_trend_span", 60))
    flat_slope_bars = int(params.get("flat_slope_bars", 12))
    flat_slope_abs = float(params.get("flat_slope_abs", 0.005))
    atr_period = int(params.get("atr_period", 14))
    exhaustion_bars = int(params.get("exhaustion_bars", 6))
    max_prior_move = float(params.get("max_prior_move", 0.08))
    follow_volume_mult = float(params.get("follow_volume_mult", 1.2))
    follow_bars = int(params.get("follow_bars", 3))
    take_atr = float(params.get("take_atr", 2.5))
    max_hold_bars = int(params.get("max_hold_bars", 30))

    prior_high = high.rolling(breakout_lookback, min_periods=breakout_lookback).max().shift(1)
    prior_low = low.rolling(breakout_lookback, min_periods=breakout_lookback).min().shift(1)
    avg_volume = volume.rolling(volume_ma_len, min_periods=volume_ma_len).mean().shift(1)
    atr = _atr(df, atr_period)

    abnormal_volume = volume >= volume_mult * avg_volume
    prior_move = close.pct_change(exhaustion_bars)
    not_up_exhausted = prior_move < max_prior_move
    not_down_exhausted = prior_move > -max_prior_move
    cont_long = (close > prior_high) & abnormal_volume & not_up_exhausted
    cont_short = (close < prior_low) & abnormal_volume & not_down_exhausted
    follow_volume = volume >= follow_volume_mult * avg_volume
    recent_long = cont_long.shift(1).rolling(follow_bars, min_periods=1).max().fillna(False)
    recent_short = cont_short.shift(1).rolling(follow_bars, min_periods=1).max().fillna(False)
    follow_long = (close > prior_high) & follow_volume & recent_long & not_up_exhausted
    follow_short = (close < prior_low) & follow_volume & recent_short & not_down_exhausted

    trend = close.ewm(span=flat_trend_span, adjust=False).mean()
    trend_slope = (trend - trend.shift(flat_slope_bars)) / trend
    flat = trend_slope.abs() < flat_slope_abs
    failed_up = cont_long.shift(1).fillna(False) & (close < prior_high) & flat
    failed_down = cont_short.shift(1).fillna(False) & (close > prior_low) & flat
    long_entry = cont_long | follow_long | failed_down
    short_entry = cont_short | follow_short | failed_up

    opens = df["open"].astype(float).to_numpy()
    highs = high.to_numpy()
    lows = low.to_numpy()
    closes = close.to_numpy()
    atrs = atr.to_numpy()
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
                stop = lows[i]
                target = c + take_atr * a
                bars_held = 0
            elif short_sig[i]:
                state = -1.0
                stop = highs[i]
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


def _apply_short_cluster_guard(
    signals: pd.DataFrame,
    data: dict[str, pd.DataFrame],
    params: dict,
) -> pd.DataFrame:
    if signals.empty:
        return signals

    min_cluster = int(params.get("short_cluster_min", 3))
    prior_threshold = float(params.get("short_cluster_prior_move", -0.045))
    core_symbols = params.get(
        "short_cluster_core",
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"],
    )
    core_set = set(core_symbols) if core_symbols else set(data.keys())

    s = signals.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True)
    wide = s.pivot_table(
        index="timestamp",
        columns="symbol",
        values="position",
        aggfunc="last",
    ).sort_index().ffill().fillna(0.0)

    prior = {}
    for symbol, df in data.items():
        prior[symbol] = df["close"].astype(float).pct_change(6).reindex(wide.index)
    prior_move = pd.DataFrame(prior).reindex(columns=wide.columns)

    prev = wide.shift(1).fillna(0.0)
    short_entries = (wide < 0) & (prev >= 0)
    eval_cols = [col for col in wide.columns if col in core_set]

    if len(eval_cols) < min_cluster:
        return signals

    cluster_bars = short_entries[eval_cols].sum(axis=1) >= min_cluster
    for ts in wide.index[cluster_bars]:
        core_entries = list(short_entries.columns[short_entries.loc[ts] & short_entries.columns.isin(eval_cols)])
        if len(core_entries) < min_cluster:
            continue
        mean_prior = float(prior_move.loc[ts, core_entries].mean())
        if not np.isfinite(mean_prior) or mean_prior > prior_threshold:
            continue

        # Flatten every same-bar short leg in the cluster until its next exit.
        for symbol in list(short_entries.columns[short_entries.loc[ts]]):
            col = wide.columns.get_loc(symbol)
            j = wide.index.get_loc(ts)
            while j < len(wide.index) and wide.iat[j, col] < 0.0:
                wide.iat[j, col] = 0.0
                j += 1

    return wide.reset_index().melt(
        id_vars="timestamp",
        var_name="symbol",
        value_name="position",
    )


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
    signals = pd.concat(frames, ignore_index=True)
    return _apply_short_cluster_guard(signals, data, params)
