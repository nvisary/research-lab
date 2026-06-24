"""Open-interest-backed perp breakout baseline.

Target thesis: real trend continuation should be backed by fresh capital flow.
When the harness provides an `open_interest` column, entries require an upper
tail OI expansion and exits fire when OI participation fades.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher


DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT"]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    "breakout_days": 3,
    "oi_lookback_hours": 24 * 14,
    "oi_delta_quantile": 0.75,
    "oi_ma_hours": 48,
    "atr_period": 14,
    "atr_trail_k": 3.0,
    "adx_period": 14,
    "adx_min": 20.0,
    "crowding_lookback_hours": 8,
    "crowding_ret_cap": 0.035,
}

PARAM_SPACE: dict = {
    "breakout_days": (3, 20),
    "oi_lookback_hours": (24 * 7, 24 * 28),
    "oi_delta_quantile": (0.75, 0.95),
    "oi_ma_hours": (12, 72),
    "atr_period": (7, 28),
    "atr_trail_k": (1.5, 5.0),
    "adx_period": (7, 28),
    "adx_min": (10.0, 35.0),
    "crowding_lookback_hours": (4, 24),
    "crowding_ret_cap": (0.015, 0.080),
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


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    breakout_days = int(params.get("breakout_days", 5))
    oi_lb = int(params.get("oi_lookback_hours", params.get("flow_lookback_hours", 24 * 14)))
    oi_q = float(params.get("oi_delta_quantile", params.get("flow_quantile", 0.90)))
    oi_ma_hours = int(params.get("oi_ma_hours", params.get("flow_ma_hours", 24)))
    atr_period = int(params.get("atr_period", 14))
    atr_trail_k = float(params.get("atr_trail_k", 3.0))
    adx_period = int(params.get("adx_period", 14))
    adx_min = float(params.get("adx_min", 20.0))
    crowd_lb = int(params.get("crowding_lookback_hours", 8))
    crowd_cap = float(params.get("crowding_ret_cap", 0.035))

    n_break = max(2, breakout_days * 24)
    upper = high.rolling(n_break, min_periods=n_break).max().shift(1)
    lower = low.rolling(n_break, min_periods=n_break).min().shift(1)

    if "open_interest" in df.columns and df["open_interest"].notna().any():
        oi = df["open_interest"].astype(float).ffill()
        oi_delta = oi.diff()
        oi_threshold = oi_delta.rolling(oi_lb, min_periods=oi_lb // 2).quantile(oi_q)
        flow_gate = (oi_delta > oi_threshold) & (oi_delta > 0)
        oi_ma = oi.rolling(oi_ma_hours, min_periods=max(4, oi_ma_hours // 2)).mean()
        flow_alive = oi >= oi_ma
    else:
        # Fallback for a repo without OI parquet yet. Keeps audits/tests usable,
        # but this is not the target research signal.
        notional = close * volume
        flow_gate = notional > notional.rolling(oi_lb, min_periods=oi_lb // 2).quantile(oi_q)
        flow_ma = notional.rolling(oi_ma_hours, min_periods=max(4, oi_ma_hours // 2)).mean()
        flow_alive = notional >= flow_ma

    recent_ret = close.pct_change(crowd_lb)
    long_not_crowded = recent_ret < crowd_cap
    short_not_crowded = recent_ret > -crowd_cap

    atr = _atr(df, atr_period)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / adx_period, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / adx_period, adjust=False).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / adx_period, adjust=False, min_periods=adx_period).mean()
    trend_strength = adx > adx_min

    daily = resample_higher(df, "1D", {"close": "last"}, target_index=df.index)
    daily_ema = daily["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    daily_bull = daily["close"] > daily_ema
    daily_bear = daily["close"] < daily_ema

    long_entry = (close > upper) & flow_gate & long_not_crowded & daily_bull & trend_strength
    short_entry = (close < lower) & flow_gate & short_not_crowded & daily_bear & trend_strength

    closes = close.to_numpy()
    uppers = upper.to_numpy()
    lowers = lower.to_numpy()
    atrs = atr.to_numpy()
    flow_ok = flow_alive.fillna(False).to_numpy()
    long_e = long_entry.fillna(False).to_numpy()
    short_e = short_entry.fillna(False).to_numpy()

    pos = np.zeros(len(df), dtype=float)
    state = 0
    trail = np.nan

    for i in range(len(df)):
        c = closes[i]
        a = atrs[i]
        if not np.isfinite(c) or not np.isfinite(a):
            pos[i] = state
            continue

        if state == 0:
            if long_e[i]:
                state = 1
                trail = c - atr_trail_k * a
            elif short_e[i]:
                state = -1
                trail = c + atr_trail_k * a
        elif state == 1:
            trail = max(trail, c - atr_trail_k * a)
            if (not flow_ok[i]) or c <= trail or c < lowers[i]:
                state = 0
                trail = np.nan
        elif state == -1:
            trail = min(trail, c + atr_trail_k * a)
            if (not flow_ok[i]) or c >= trail or c > uppers[i]:
                state = 0
                trail = np.nan
        pos[i] = state

    return pd.Series(pos, index=df.index).shift(1).fillna(0.0)


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _signals_for_symbol(df, params)
        frames.append(
            pd.DataFrame(
                {"timestamp": df.index, "symbol": symbol, "position": pos.values}
            )
        )
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
