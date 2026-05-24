"""Mean Reversion — Bollinger Bands + RSI.

Thesis: Price returns to the mean after extreme volatility expansions (Bollinger Bands)
confirmed by oversold/overbought conditions (RSI).
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
    "bb_period": 20,
    "bb_std": 2.0,
    "rsi_period": 14,
    "rsi_os": 30,
    "rsi_ob": 70,
}

PARAM_SPACE: dict = {
    "bb_period": (10, 50),
    "bb_std": (1.5, 3.0),
    "rsi_period": (7, 21),
    "rsi_os": (20, 40),
    "rsi_ob": (60, 80),
}


def _signals_for_symbol(df: pd.DataFrame, params: dict, symbol: str) -> pd.Series:
    close = df["close"]

    bb_period = int(params.get("bb_period", 20))
    bb_std = float(params.get("bb_std", 2.0))
    rsi_period = int(params.get("rsi_period", 14))
    rsi_os = float(params.get("rsi_os", 30))
    rsi_ob = float(params.get("rsi_ob", 70))

    # Bollinger Bands
    basis = close.rolling(bb_period).mean()
    std = close.rolling(bb_period).std()
    upper = basis + (std * bb_std)
    lower = basis - (std * bb_std)

    # RSI
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    # Entries
    long_entry = (close < lower) & (rsi < rsi_os)
    short_entry = (close > upper) & (rsi > rsi_ob)

    curr_pos = 0.0
    positions = []

    for i in range(len(df)):
        if curr_pos == 0:
            if long_entry.iloc[i]:
                curr_pos = 1.0
            elif short_entry.iloc[i]:
                curr_pos = -1.0
        elif curr_pos == 1.0:
            # Exit at basis
            if close.iloc[i] >= basis.iloc[i]:
                curr_pos = 0.0
        elif curr_pos == -1.0:
            # Exit at basis
            if close.iloc[i] <= basis.iloc[i]:
                curr_pos = 0.0
        positions.append(curr_pos)

    return pd.Series(positions, index=df.index).shift(1).fillna(0.0)


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
