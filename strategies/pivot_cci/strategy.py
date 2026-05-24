"""Z-Score Mean Reversion with EMA Trend Slope Filter.

Thesis: Price reverts to the mean after overextending statistically,
as measured by Z-Score, filtered by EMA slope to avoid counter-trend entries.
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
]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    "z_period": 94,
    "z_threshold": 2.55664,
    "ema_period": 158,
    "slope_threshold": 0.00130,
    "long_only": 0,
}

PARAM_SPACE: dict = {
    "z_period": (20, 100),
    "z_threshold": (1.5, 3.0),
    "ema_period": (100, 400),
    "slope_threshold": (0.0, 0.005),
    "long_only": (0, 1),
}


def _signals_for_symbol(df: pd.DataFrame, params: dict, symbol: str) -> pd.Series:
    close = df["close"]

    # 1. Z-Score
    mean = close.rolling(params["z_period"]).mean()
    std = close.rolling(params["z_period"]).std()
    z_score = (close - mean) / std

    # 2. Trend Slope Filter
    ema = close.ewm(span=params["ema_period"], adjust=False).mean()
    slope = (ema - ema.shift(5)) / 5

    # 3. Signals
    # Long: Z < -threshold AND slope is not strongly negative (or is positive)
    long_cond = (z_score < -params["z_threshold"]) & (
        slope > -params["slope_threshold"]
    )
    # Short: Z > threshold AND slope is not strongly positive
    short_cond = (z_score > params["z_threshold"]) & (slope < params["slope_threshold"])

    pos = pd.Series(0, index=df.index)
    for i in range(1, len(df)):
        if pos.iloc[i - 1] == 0:
            if long_cond.iloc[i]:
                pos.iloc[i] = 1
            elif not params["long_only"] and short_cond.iloc[i]:
                pos.iloc[i] = -1
        elif pos.iloc[i - 1] == 1:
            if z_score.iloc[i] >= 0:
                pos.iloc[i] = 0
            else:
                pos.iloc[i] = 1
        elif pos.iloc[i - 1] == -1:
            if z_score.iloc[i] <= 0:
                pos.iloc[i] = 0
            else:
                pos.iloc[i] = -1

    return pos.shift(1).fillna(0.0)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _signals_for_symbol(df, params, symbol)
        frames.append(
            pd.DataFrame(
                {"timestamp": df.index, "symbol": symbol, "position": pos.values}
            )
        )
    return (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=["timestamp", "symbol", "position"])
    )
