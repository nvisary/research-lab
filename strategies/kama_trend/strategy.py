"""kama_trend — Trend-following strategy using KAMA and Momentum filter.

Logic:
- KAMA (Kaufman's Adaptive Moving Average) as the primary trend line.
- Efficiency Ratio (ER) from KAMA used as a trend strength filter (ER rising).
- CCI on a higher timeframe (e.g., 15m) as a momentum gate.
- Long Entry: Close > KAMA AND ER rising AND CCI > 100.
- Long Exit: Close < KAMA.
"""

import numpy as np
import pandas as pd

from harness.utils import resample_higher

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "1min"

DEFAULT_PARAMS: dict = {
    "kama_n": 10,
    "kama_fast": 2,
    "kama_slow": 30,
    "cci_period": 20,
    "cci_tf": "15min",
    "long_only": 1,
    "vol_target": 0.01,
    "atr_period": 14,
}

PARAM_SPACE: dict = {
    "kama_n": (5, 20),
    "kama_fast": (2, 5),
    "kama_slow": (20, 50),
    "cci_period": (10, 30),
    "long_only": (0, 1),
}


def compute_kama(price: pd.Series, n: int, fast: int, slow: int) -> pd.Series:
    # 1. Efficiency Ratio (ER)
    change = (price - price.shift(n)).abs()
    volatility = (price - price.shift(1)).abs().rolling(window=n).sum()
    er = change / volatility.where(volatility > 0)

    # 2. Smoothing Constant (SC)
    sc_fastest = 2.0 / (fast + 1.0)
    sc_slowest = 2.0 / (slow + 1.0)
    sc = (er * (sc_fastest - sc_slowest) + sc_slowest) ** 2

    # 3. KAMA calculation
    price_arr = price.values
    sc_arr = sc.fillna(0).values
    kama_arr = np.zeros_like(price_arr)

    # Find first valid index (after ER lookback)
    start_idx = n
    if start_idx < len(price_arr):
        kama_arr[start_idx] = price_arr[start_idx]
        for i in range(start_idx + 1, len(price_arr)):
            # KAMA(i) = KAMA(i-1) + SC(i) * (Price(i) - KAMA(i-1))
            kama_arr[i] = kama_arr[i - 1] + sc_arr[i] * (price_arr[i] - kama_arr[i - 1])

    kama_arr[:start_idx] = np.nan
    return pd.Series(kama_arr, index=price.index)


def compute_cci(df: pd.DataFrame, period: int) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    sma = tp.rolling(period).mean()
    # Mean Absolute Deviation
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    cci = (tp - sma) / (0.015 * mad.where(mad > 0))
    return cci


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"]
    high, low = df["high"], df["low"]

    kama_n = int(params.get("kama_n", 10))
    kama_fast = int(params.get("kama_fast", 2))
    kama_slow = int(params.get("kama_slow", 30))
    cci_period = int(params.get("cci_period", 20))
    cci_tf = params.get("cci_tf", "15min")
    long_only = int(params.get("long_only", 1)) == 1

    # 1. KAMA & ER
    kama = compute_kama(close, kama_n, kama_fast, kama_slow)

    # Recalculate ER for the rising check (avoiding redundant compute_kama calls if we extracted it)
    change = (close - close.shift(kama_n)).abs()
    volatility = (close - close.shift(1)).abs().rolling(window=kama_n).sum()
    er = change / volatility.where(volatility > 0)
    er_rising = er > er.shift(1)

    # 2. CCI on 15m (resampled)
    # resample_higher returns a df indexed by df.index if target_index is passed
    df_res = resample_higher(
        df,
        cci_tf,
        {"high": "max", "low": "min", "close": "last"},
        target_index=df.index,
    )
    cci = compute_cci(df_res, cci_period)

    # 3. Entry Signals
    long_entry = (close > kama) & er_rising & (cci > 100)
    short_entry = (close < kama) & er_rising & (cci < -100)

    # 4. State Machine for Position Tracking
    pos_arr = np.zeros(len(df))
    current_pos = 0.0

    long_entry_v = long_entry.values
    short_entry_v = short_entry.values
    close_v = close.values
    kama_v = kama.values

    # Iterative pass to handle entry/exit logic
    # Note: KAMA can be NaN in the beginning
    for i in range(1, len(df)):
        if np.isnan(kama_v[i]):
            continue

        if current_pos == 0:
            if long_entry_v[i]:
                current_pos = 1.0
            elif not long_only and short_entry_v[i]:
                current_pos = -1.0
        elif current_pos == 1.0:
            # Exit long if price crosses below KAMA
            if close_v[i] < kama_v[i]:
                current_pos = 0.0
        elif current_pos == -1.0:
            # Exit short if price crosses above KAMA
            if close_v[i] > kama_v[i]:
                current_pos = 0.0
        pos_arr[i] = current_pos

    pos = pd.Series(pos_arr, index=df.index)

    # 5. Volatility Sizing (Optional, standard for the harness)
    atr_period = int(params.get("atr_period", 14))
    vol_target = float(params.get("vol_target", 0.01))

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(span=atr_period, adjust=False, min_periods=atr_period).mean()
    atr_pct = (atr / close).fillna(0.01)  # fallback to 1% if NaN

    # size = vol_target / current_atr_pct
    size_mult = (vol_target / atr_pct).clip(lower=0.1, upper=1.0)

    pos = pos * size_mult

    # Shift-by-one to trade on NEXT bar open
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
