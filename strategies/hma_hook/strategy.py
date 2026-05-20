"""hma_hook — Hull Moving Average (HMA) direction change (hook) strategy.

Baseline thesis: Use HMA to detect trend reversals with minimal lag.
- HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))
- Long when HMA starts rising (HMA[t] > HMA[t-1]).
- Short when HMA starts falling (HMA[t] < HMA[t-1]).
- Hold position while direction is maintained.
"""

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "length": 30,
    "trend_span": 200,
    "slope_lb": 12,
    "vol_target": 0.008,
    "vol_q": 0.70,
    "long_only": 0,
}

PARAM_SPACE: dict = {
    "length": (10, 100),
    "trend_span": (50, 400),
    "slope_lb": (6, 96),
    "vol_target": (0.005, 0.02),
    "vol_q": (0.5, 0.95),
    "long_only": (0, 1),
}


def wma(s: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average."""
    if period < 1:
        return pd.Series(np.nan, index=s.index)
    weights = np.arange(1, period + 1).astype(float)
    weights /= weights.sum()
    return s.rolling(period).apply(lambda x: np.sum(x * weights), raw=True)


def hma(s: pd.Series, period: int) -> pd.Series:
    """Hull Moving Average."""
    if period < 2:
        return s
    half_length = period // 2
    sqrt_length = int(np.sqrt(period))

    wma_half = wma(s, half_length)
    wma_full = wma(s, period)

    diff = 2 * wma_half - wma_full
    return wma(diff, sqrt_length)


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    """Per-symbol position series."""
    close = df["close"]
    high, low = df["high"], df["low"]

    length = int(params.get("length", 30))
    trend_span = int(params.get("trend_span", 200))
    slope_lb = int(params.get("slope_lb", 12))
    long_only = int(params.get("long_only", 0)) == 1
    vol_target = float(params.get("vol_target", 0.008))
    vol_q = float(params.get("vol_q", 0.70))

    h = hma(close, length)

    # Direction change (hook)
    rising = h > h.shift(1)
    falling = h < h.shift(1)

    # Trend filter: EMA + Slope
    ema_trend = close.ewm(span=trend_span, adjust=False, min_periods=trend_span).mean()
    ema_slope = ema_trend - ema_trend.shift(slope_lb)
    up_regime = (close > ema_trend) & (ema_slope > 0)
    dn_regime = (close < ema_trend) & (ema_slope < 0)

    # Extreme-vol gate: skip entries in top vol_q quantile of ATR%.
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(span=14, adjust=False, min_periods=14).mean()
    atr_pct = atr / close
    vol_thresh = atr_pct.rolling(200, min_periods=200).quantile(vol_q)
    vol_ok = atr_pct <= vol_thresh

    pos = pd.Series(0.0, index=df.index)
    pos[rising & up_regime & vol_ok] = 1.0
    if not long_only:
        pos[falling & dn_regime & vol_ok] = -1.0

    # Vol-targeted sizing
    # Clip to avoid explosive sizing in very-low-vol bars
    size_mult = (vol_target / atr_pct).clip(lower=0.3, upper=1.0)

    pos = pos * size_mult.fillna(0.3)
    return pos.shift(1).fillna(0.0)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _signals_for_symbol(df, params)
        frames.append(
            pd.DataFrame(
                {
                    "timestamp": df.index,
                    "symbol": symbol,
                    "position": pos.values,
                }
            )
        )
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
