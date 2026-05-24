"""Volatility Breakout Strategy (Toby Crabel Style).

Logic:
- Capture momentum after consolidation.
- Anchor: Daily Open (00:00 UTC).
- Threshold: ATR-based range.
- Filter: Trend + Loose Volatility Expansion.
- Exit: ATR-based trailing stop.
"""

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "1min"

DEFAULT_PARAMS: dict = {
    "atr_period": 600,  # 10 hours on 1min
    "k": 1.0,  # Breakout multiplier
    "v_mult": 1.1,  # Volume filter multiplier
    "stop_mult": 4.0,  # Trailing stop multiplier
    "trend_fast": 60,  # 1 hour
    "trend_slow": 240,  # 4 hours
    "vol_target": 0.015,
}

PARAM_SPACE: dict = {
    "k": (0.5, 1.5),
    "v_mult": (1.0, 1.5),
    "stop_mult": (3.0, 6.0),
}


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    atr_p = int(params.get("atr_period", 600))
    k = float(params.get("k", 1.0))
    v_mult = float(params.get("v_mult", 1.1))
    stop_mult = float(params.get("stop_mult", 4.0))
    t_fast = int(params.get("trend_fast", 60))
    t_slow = int(params.get("trend_slow", 240))
    vol_target = float(params.get("vol_target", 0.015))

    # 1. Indicators
    # Daily Open (00:00 UTC)
    daily_open = df["open"].groupby(df.index.date).transform("first")

    # ATR
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / atr_p, adjust=False, min_periods=atr_p).mean()

    # Relative Volatility Filter (Loosened)
    atr_avg = atr.rolling(window=atr_p * 2).mean()
    vol_ok = atr < atr_avg * 1.2

    # Average Volume
    avg_vol = volume.rolling(window=atr_p, min_periods=atr_p).mean()

    # Trend Filter
    ema_fast = close.ewm(span=t_fast, adjust=False).mean()
    ema_slow = close.ewm(span=t_slow, adjust=False).mean()
    trend_up = ema_fast > ema_slow
    trend_dn = ema_fast < ema_slow

    # 2. Base Signals (Entry)
    long_threshold = daily_open + (k * atr)
    short_threshold = daily_open - (k * atr)

    long_breakout = (
        (close > long_threshold)
        & (close.shift(1) <= long_threshold)
        & (volume > v_mult * avg_vol)
        & vol_ok
        & trend_up
    )
    short_breakout = (
        (close < short_threshold)
        & (close.shift(1) >= short_threshold)
        & (volume > v_mult * avg_vol)
        & vol_ok
        & trend_dn
    )

    # 3. State Machine for Positions and Trailing Stops
    pos_arr = np.zeros(len(df))
    current_pos = 0.0
    stop_price = 0.0
    hh = 0.0
    ll = float("inf")

    close_vals = close.values
    high_vals = high.values
    low_vals = low.values
    atr_vals = atr.values
    long_sig = long_breakout.values
    short_sig = short_breakout.values

    for i in range(len(df)):
        if i < max(atr_p * 2, t_slow):
            continue

        if current_pos == 0:
            if long_sig[i]:
                current_pos = 1.0
                hh = high_vals[i]
                stop_price = close_vals[i] - (stop_mult * atr_vals[i])
            elif short_sig[i]:
                current_pos = -1.0
                ll = low_vals[i]
                stop_price = close_vals[i] + (stop_mult * atr_vals[i])
        else:
            exit_trade = False
            if current_pos == 1.0:
                hh = max(hh, high_vals[i])
                trail_stop = hh - (stop_mult * atr_vals[i])
                stop_price = max(stop_price, trail_stop)

                if close_vals[i] < stop_price:
                    exit_trade = True
                elif short_sig[i]:  # Reversal
                    exit_trade = True

            elif current_pos == -1.0:
                ll = min(ll, low_vals[i])
                trail_stop = ll + (stop_mult * atr_vals[i])
                stop_price = min(stop_price, trail_stop)

                if close_vals[i] > stop_price:
                    exit_trade = True
                elif long_sig[i]:  # Reversal
                    exit_trade = True

            if exit_trade:
                current_pos = 0.0
                if long_sig[i]:
                    current_pos = 1.0
                    hh = high_vals[i]
                    stop_price = close_vals[i] - (stop_mult * atr_vals[i])
                elif short_sig[i]:
                    current_pos = -1.0
                    ll = low_vals[i]
                    stop_price = close_vals[i] + (stop_mult * atr_vals[i])

        pos_arr[i] = current_pos

    # 4. Sizing
    atr_pct = (atr / close).clip(lower=0.001)
    size_mult = (vol_target / atr_pct).clip(lower=0.1, upper=2.0)

    pos_series = pd.Series(pos_arr * size_mult.values, index=df.index)

    return pos_series.shift(1).fillna(0.0)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
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
