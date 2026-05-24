"""Mean Reversion — R1/S1 Pivot Mean Reversion.

Thesis: Price often overextends past R1/S1 and reverts to the Pivot Point (P).
"""

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "1000PEPEUSDT",
    "SUIUSDT",
    "ADAUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "WLDUSDT",
    "NEARUSDT",
    "BNBUSDT",
    "LTCUSDT",
    "TIAUSDT",
    "ARBUSDT",
    "AAVEUSDT",
    "TONUSDT",
    "INJUSDT",
    "BCHUSDT",
]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    "sl_pct": 0.03,
    "tp_pct": 0.01,
    "rsi_period": 14,
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "long_only": 0,
}

PARAM_SPACE: dict = {
    "sl_pct": (0.01, 0.05),
    "tp_pct": (0.005, 0.03),
    "rsi_period": (10, 20),
    "rsi_overbought": (60, 80),
    "rsi_oversold": (20, 40),
    "long_only": (0, 1),
}


def calculate_rsi(close, period=14):
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def _signals_for_symbol(df: pd.DataFrame, params: dict, symbol: str) -> pd.Series:
    close = df["close"]

    sl_pct = float(params.get("sl_pct", 0.03))
    tp_pct = float(params.get("tp_pct", 0.01))
    rsi_period = int(params.get("rsi_period", 14))
    rsi_ob = float(params.get("rsi_overbought", 70.0))
    rsi_os = float(params.get("rsi_oversold", 30.0))
    long_only = int(params.get("long_only", 0)) == 1

    daily = df.resample("D").agg({"high": "max", "low": "min", "close": "last"})
    daily_prev = daily.shift(1)

    p = (daily_prev["high"] + daily_prev["low"] + daily_prev["close"]) / 3
    r1 = (2 * p) - daily_prev["low"]
    s1 = (2 * p) - daily_prev["high"]

    p = p.reindex(df.index, method="ffill")
    r1 = r1.reindex(df.index, method="ffill")
    s1 = s1.reindex(df.index, method="ffill")

    rsi = calculate_rsi(close, rsi_period)

    # Long Entry: Price < S1 AND RSI < Oversold
    long_entry = (close < s1) & (rsi < rsi_os)
    # Short Entry: Price > R1 AND RSI > Overbought
    short_entry = (close > r1) & (rsi > rsi_ob)

    curr_pos = 0.0
    entry_price = 0.0
    positions = []

    for i in range(len(df)):
        if curr_pos == 0:
            if long_entry.iloc[i]:
                curr_pos = 1.0
                entry_price = close.iloc[i]
            elif not long_only and short_entry.iloc[i]:
                curr_pos = -1.0
                entry_price = close.iloc[i]
        elif curr_pos == 1.0:
            # Exit: Price >= Pivot OR SL OR TP
            if (
                (close.iloc[i] >= p.iloc[i])
                or (close.iloc[i] <= entry_price * (1 - sl_pct))
                or (close.iloc[i] >= entry_price * (1 + tp_pct))
            ):
                curr_pos = 0.0
        elif curr_pos == -1.0:
            # Exit: Price <= Pivot OR SL OR TP
            if (
                (close.iloc[i] <= p.iloc[i])
                or (close.iloc[i] >= entry_price * (1 + sl_pct))
                or (close.iloc[i] <= entry_price * (1 - tp_pct))
            ):
                curr_pos = 0.0
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
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
