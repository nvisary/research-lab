"""Pivot Points Mean Reversion with CCI confirmation.

Thesis: Price often reverts to the Mean (Pivot Point) after touching Support/Resistance
levels (S1/R1, S2/R2), especially when momentum (CCI) indicates an overextended state.
"""

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT"]
DEFAULT_TF: str = "1h"
from datafeed.loader import load_funding

DEFAULT_PARAMS: dict = {
    "cci_period": 14,
    "cci_threshold": 80,
    "rsi_period": 14,
    "rsi_threshold": 40,
    "funding_threshold": 0.0005,
    "pivot_level": 1,
    "trend_period": 200,
    "cci_exit": 40,
    "long_only": 0,
}

PARAM_SPACE: dict = {
    "cci_period": (10, 30),
    "cci_threshold": (50, 150),
    "rsi_period": (7, 21),
    "rsi_threshold": (20, 50),
    "funding_threshold": (0.0001, 0.001),
    "pivot_level": (1, 2),
    "trend_period": (50, 400),
    "cci_exit": (0, 80),
    "long_only": (0, 1),
}


def _signals_for_symbol(df: pd.DataFrame, params: dict, symbol: str) -> pd.Series:
    """Per-symbol position series."""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    cci_period = int(params.get("cci_period", 14))
    cci_threshold = float(params.get("cci_threshold", 80))
    rsi_period = int(params.get("rsi_period", 14))
    rsi_threshold = float(params.get("rsi_threshold", 40))
    funding_threshold = float(params.get("funding_threshold", 0.0005))
    pivot_level = int(params.get("pivot_level", 1))
    trend_period = int(params.get("trend_period", 200))
    cci_exit = float(params.get("cci_exit", 40))
    long_only = int(params.get("long_only", 0)) == 1

    # 1. CCI Calculation
    tp = (high + low + close) / 3
    ma_tp = tp.rolling(cci_period).mean()

    def mad(x):
        return np.abs(x - x.mean()).mean()

    mean_dev = tp.rolling(cci_period).apply(mad, raw=True)
    cci = (tp - ma_tp) / (0.015 * mean_dev)

    # 2. RSI Calculation
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=rsi_period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=rsi_period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(50)

    # 3. Funding Rate Bias
    try:
        funding = load_funding(symbol, df.index[0], df.index[-1])
        funding = funding.reindex(df.index, method="ffill").fillna(0)
        funding_rate = funding["rate"]
    except Exception:
        funding_rate = pd.Series(0.0, index=df.index)

    # Allow Long only if funding is not extremely positive
    long_funding_ok = funding_rate < funding_threshold
    # Allow Short only if funding is not extremely negative
    short_funding_ok = funding_rate > -funding_threshold

    # 4. Trend Filter
    ema_trend = close.ewm(span=trend_period, adjust=False).mean()
    up_trend = close > ema_trend
    dn_trend = close < ema_trend

    # 5. Daily Pivot Points
    # ... (rest of pivot logic)
    daily = df.resample("D").agg({"high": "max", "low": "min", "close": "last"})
    daily_prev = daily.shift(1)

    p = (daily_prev["high"] + daily_prev["low"] + daily_prev["close"]) / 3
    s1 = 2 * p - daily_prev["high"]
    r1 = 2 * p - daily_prev["low"]
    s2 = p - (daily_prev["high"] - daily_prev["low"])
    r2 = p + (daily_prev["high"] - daily_prev["low"])

    p = p.reindex(df.index, method="ffill")
    s1 = s1.reindex(df.index, method="ffill")
    r1 = r1.reindex(df.index, method="ffill")
    s2 = s2.reindex(df.index, method="ffill")
    r2 = r2.reindex(df.index, method="ffill")

    if pivot_level == 1:
        support, resistance = s1, r1
    else:
        support, resistance = s2, r2

    # Long: Price < Support AND CCI < -threshold AND RSI < threshold AND CCI rising AND Up-Trend AND Funding OK
    long_cond = (
        (low < support)
        & (cci < -cci_threshold)
        & (rsi < rsi_threshold)
        & (cci > cci.shift(1))
        & up_trend
        & long_funding_ok
    )

    # Short: Price > Resistance AND CCI > threshold AND RSI > (100-rsi_threshold) AND CCI falling AND Dn-Trend AND Funding OK
    short_cond = (
        (high > resistance)
        & (cci > cci_threshold)
        & (rsi > (100 - rsi_threshold))
        & (cci < cci.shift(1))
        & dn_trend
        & short_funding_ok
    )

    curr_pos = 0.0
    positions = []
    for i in range(len(df)):
        if curr_pos == 0:
            if long_cond.iloc[i]:
                curr_pos = 1.0
            elif not long_only and short_cond.iloc[i]:
                curr_pos = -1.0
        elif curr_pos == 1.0:
            # Exit if price hits P OR CCI recovers past -cci_exit (faster than 0)
            # OR new Short. Exiting before the full CCI→0 crawl captures more of
            # the bounce and cuts the time-drag tail (diagnostic: CCI0 exits kept
            # only 0.37 of MFE).
            if close.iloc[i] >= p.iloc[i] or cci.iloc[i] >= -cci_exit or short_cond.iloc[i]:
                curr_pos = 0.0
                if not long_only and short_cond.iloc[i]:
                    curr_pos = -1.0
        elif curr_pos == -1.0:
            # Exit if price hits P OR CCI recovers past +cci_exit OR new Long
            if close.iloc[i] <= p.iloc[i] or cci.iloc[i] <= cci_exit or long_cond.iloc[i]:
                curr_pos = 0.0
                if long_cond.iloc[i]:
                    curr_pos = 1.0
        positions.append(curr_pos)

    pos = pd.Series(positions, index=df.index)
    return pos.shift(1).fillna(0.0)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _signals_for_symbol(df, params, symbol)
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
