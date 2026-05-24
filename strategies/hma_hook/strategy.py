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
    "regime_length": 200,
    "vol_exp_thresh": 1.25,  # Expansion trigger
    "vol_exp_span": 50,  # Lookback span
    "vol_target": 0.008,
    "vol_q": 0.70,
    "long_only": 0,
}

PARAM_SPACE: dict = {
    "length": (10, 100),
    "regime_length": (100, 500),
    "vol_exp_thresh": (1.05, 2.0),
    "vol_exp_span": (20, 100),
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
    reg_len = int(params.get("regime_length", 200))
    vol_exp = float(params.get("vol_exp_thresh", 1.25))
    vol_span = int(params.get("vol_exp_span", 50))
    long_only = int(params.get("long_only", 0)) == 1
    vol_target = float(params.get("vol_target", 0.008))
    vol_q = float(params.get("vol_q", 0.70))

    # Signal HMA
    h = hma(close, length)
    rising = h > h.shift(1)
    falling = h < h.shift(1)

    # Regime HMA
    h_reg = hma(close, reg_len)
    up_regime = (close > h_reg) & (h_reg > h_reg.shift(1))
    dn_regime = (close < h_reg) & (h_reg < h_reg.shift(1))

    # RSI extremes
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi_ok_long = rsi < 70
    rsi_ok_short = rsi > 30

    # Extreme-vol gate & Vol expansion trigger
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
    atr_ma = atr.rolling(vol_span).mean()

    atr_pct = atr / close
    vol_thresh = atr_pct.rolling(200, min_periods=200).quantile(vol_q)
    vol_ok = (atr_pct <= vol_thresh) & (atr < (atr_ma * vol_exp))

    pos = pd.Series(0.0, index=df.index)
    pos[rising & up_regime & rsi_ok_long & vol_ok] = 1.0
    if not long_only:
        pos[falling & dn_regime & rsi_ok_short & vol_ok] = -1.0

    # Vol-targeted sizing
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
