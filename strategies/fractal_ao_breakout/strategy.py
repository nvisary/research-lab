"""fractal_ao_breakout — Williams Fractal breakout with Awesome Oscillator confirmation.

Baseline thesis: trend-following using fractals for support/resistance levels
and Awesome Oscillator for momentum confirmation.
- Williams Fractal (5-bar): high/low of a 5-bar window.
- Awesome Oscillator (AO): SMA(median, 5) - SMA(median, 34).
- Long when close breaks above the last Up Fractal AND AO > 0.
- Short when close breaks below the last Down Fractal AND AO < 0.
"""

import numpy as np
import pandas as pd

from harness.utils import resample_higher

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT"]
DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "ao_fast": 5,
    "ao_slow": 13,
    "fractal_window": 5,
    "long_only": 1,
    "vol_target": 0.02,
    "adx_min": 25,
    "htf_ema": 50,
    "continuation_ema": 20,
    "continuation_atr_mult": 0.6,
}

PARAM_SPACE: dict = {
    "ao_fast": (3, 15),
    "ao_slow": (10, 60),
    "fractal_window": (5, 5),  # Usually fixed at 5 for Williams
    "long_only": (0, 1),
    "adx_min": (10, 40),
    "htf_ema": (20, 200),
    "continuation_ema": (10, 50),
    "continuation_atr_mult": (0.25, 2.0),
}


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    """Per-symbol position series. Indexed by df's timestamp."""
    close = df["close"]
    high, low = df["high"], df["low"]

    ao_fast = int(params.get("ao_fast", 5))
    ao_slow = int(params.get("ao_slow", 21))  # champion from iter 4
    long_only = int(params.get("long_only", 0)) == 1
    adx_min = float(params.get("adx_min", 25))
    htf_ema_period = int(params.get("htf_ema", 50))
    continuation_ema_period = int(params.get("continuation_ema", 20))
    continuation_atr_mult = float(params.get("continuation_atr_mult", 0.6))

    # --- Awesome Oscillator ---
    median_price = (high + low) / 2
    ao = (
        median_price.rolling(window=ao_fast).mean()
        - median_price.rolling(window=ao_slow).mean()
    )

    # --- Williams Fractals ---
    # Up fractal: high[t-2] is higher than high[t-4, t-3, t-1, t]
    # We shift high to align central peak at t
    up_fractal = (
        (high.shift(2) > high.shift(4))
        & (high.shift(2) > high.shift(3))
        & (high.shift(2) > high.shift(1))
        & (high.shift(2) > high)
    )
    # The high value of the up fractal
    up_fractal_val = high.shift(2).where(up_fractal).ffill()

    # Down fractal: low[t-2] is lower than low[t-4, t-3, t-1, t]
    dn_fractal = (
        (low.shift(2) < low.shift(4))
        & (low.shift(2) < low.shift(3))
        & (low.shift(2) < low.shift(1))
        & (low.shift(2) < low)
    )
    # The low value of the down fractal
    dn_fractal_val = low.shift(2).where(dn_fractal).ffill()

    # --- ADX Filter ---
    atr_period = 14
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = high - high.shift(1)
    dn_move = low.shift(1) - low

    plus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up_move > dn_move) & (up_move > 0)] = up_move

    minus_dm = pd.Series(0.0, index=df.index)
    minus_dm[(dn_move > up_move) & (dn_move > 0)] = dn_move

    alpha = 1.0 / atr_period
    atr_wilder = tr.ewm(alpha=alpha, adjust=False, min_periods=atr_period).mean()
    plus_di = 100 * (
        plus_dm.ewm(alpha=alpha, adjust=False, min_periods=atr_period).mean()
        / atr_wilder
    )
    minus_di = 100 * (
        minus_dm.ewm(alpha=alpha, adjust=False, min_periods=atr_period).mean()
        / atr_wilder
    )
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=atr_period).mean()
    adx_filter = adx > adx_min

    # --- HTF Trend Gate ---
    df_1d = resample_higher(df, "1d", {"close": "last"}, target_index=df.index)
    ema_1d = df_1d["close"].ewm(span=htf_ema_period, adjust=False).mean()
    trend_up = df_1d["close"] > ema_1d
    trend_dn = df_1d["close"] < ema_1d

    # --- Signals ---
    # Price breaks above/below the last known fractal
    # confirmed by AO sign, ADX trend strength, and HTF trend
    breakout_long = close > up_fractal_val
    continuation_ema = close.ewm(span=continuation_ema_period, adjust=False).mean()
    atr_for_signal = tr.ewm(span=atr_period, adjust=False, min_periods=atr_period).mean()
    continuation_long = (
        (close > continuation_ema)
        & (close <= continuation_ema + continuation_atr_mult * atr_for_signal)
        & (close > close.shift(1))
    )
    long_pos = (
        (breakout_long | continuation_long)
        & (ao > 0)
        & adx_filter
        & trend_up
    )
    short_pos = (close < dn_fractal_val) & (ao < 0) & adx_filter & trend_dn

    # --- Sizing (Optional vol-targeting) ---
    # Use ATR% for scaling if vol_target is provided
    atr = tr.ewm(span=atr_period, adjust=False, min_periods=atr_period).mean()
    atr_pct = (atr / close).replace(0, np.nan)

    vol_target = float(params.get("vol_target", 0.01))
    size_mult = (vol_target / atr_pct).clip(lower=0.3, upper=1.0)

    pos = pd.Series(0.0, index=df.index)
    pos[long_pos] = 1.0
    if not long_only:
        pos[short_pos] = -1.0

    pos = pos * size_mult.fillna(0.3)

    # Shift by 1 to trade on next open (standard practice in this harness)
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
