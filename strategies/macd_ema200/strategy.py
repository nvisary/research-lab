"""macd_ema200 — MACD signal-line crossover gated by EMA200 trend filter.

Baseline thesis: classic textbook trend-following.
- MACD = EMA(fast) - EMA(slow); signal = EMA(MACD, signal_span).
- Long when MACD crosses above signal AND close > EMA200 (uptrend regime).
- Short when MACD crosses below signal AND close < EMA200 (downtrend regime).
- Hold position between crossovers (state-machine style); flat when filter rejects.

Symbol-agnostic: the same calculation is applied independently per symbol in
``data``. DEFAULT_SYMBOLS pins the iter loop to BTCUSDT, but ``runner.sweep``
can run this on any subset of the available universe to test robustness.
"""

import numpy as np
import pandas as pd


DEFAULT_SYMBOLS: list[str] = ["BTCUSDT"]
DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "fast": 12,
    "slow": 26,
    "signal": 9,
    "trend": 200,
    "slope_lb": 24,
    "long_only": 0,
}

PARAM_SPACE: dict = {
    "fast": (4, 50),
    "slow": (20, 100),
    "signal": (3, 30),
    "trend": (50, 400),
    "slope_lb": (6, 96),
    "long_only": (0, 1),
}


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    """Per-symbol position series. Indexed by df's timestamp, values in
    [-1, 1] after vol sizing + shift-by-one-bar trade-on-next-open."""
    close = df["close"]
    high, low = df["high"], df["low"]

    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal_span = int(params.get("signal", 9))
    trend = int(params.get("trend", 200))
    long_only = int(params.get("long_only", 0)) == 1
    atr_period = int(params.get("atr_period", 14))
    vol_lookback = int(params.get("vol_lookback", 200))
    vol_q = float(params.get("vol_q", 0.70))

    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal_span, adjust=False, min_periods=signal_span).mean()

    ema_trend = close.ewm(span=trend, adjust=False, min_periods=trend).mean()

    # Combine price-vs-EMA200 with EMA200 slope (over `slope_lb` bars) —
    # slope kills flat-regime entries that price-only filter admits in chop.
    slope_lb = max(int(params.get("slope_lb", 24)), 1)
    ema_slope = ema_trend - ema_trend.shift(slope_lb)
    up_regime = (close > ema_trend) & (ema_slope > 0)
    dn_regime = (close < ema_trend) & (ema_slope < 0)

    # Extreme-vol gate: skip entries in top vol_q quantile of ATR%.
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=atr_period, adjust=False, min_periods=atr_period).mean()
    atr_pct = atr / close
    vol_thresh = atr_pct.rolling(vol_lookback, min_periods=vol_lookback).quantile(vol_q)
    vol_ok = atr_pct <= vol_thresh

    long_pos = (macd > sig) & up_regime & vol_ok
    short_pos = (macd < sig) & dn_regime & vol_ok

    # Vol-targeted sizing: scale by vol_target / current atr% (clipped to avoid
    # explosive sizing in very-low-vol bars).
    vol_target = float(params.get("vol_target", 0.008))
    size_floor = float(params.get("size_floor", 0.3))
    size_mult = (vol_target / atr_pct).clip(lower=size_floor, upper=1.0)

    pos = pd.Series(0.0, index=df.index)
    pos[long_pos] = 1.0
    if not long_only:
        pos[short_pos] = -1.0
    pos = pos * size_mult.fillna(size_floor)
    return pos.shift(1).fillna(0.0)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _signals_for_symbol(df, params)
        frames.append(pd.DataFrame({
            "timestamp": df.index,
            "symbol": symbol,
            "position": pos.values,
        }))
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
