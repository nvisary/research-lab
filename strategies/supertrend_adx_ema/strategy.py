"""Optimized Mean-Reversion (Fast BB + Sensitive RSI) - Multi-Symbol.

Goal: Maximize PnL and Activity in ranges.
Logic:
- Symbols: BTC, ETH, SOL.
- Indicators: Fast Bollinger Bands (14, 2.0), Sensitive RSI (10).
- Entry:
    1. Long: Price crosses below Lower BB AND RSI < 30.
    2. Short: Price crosses above Upper BB AND RSI > 70.
- Exit:
    1. Long: Price reaches BB Midline OR RSI > 50.
    2. Short: Price reaches BB Midline OR RSI < 50.
- Fast settings catch more intraday oscillations to maximize PnL.
"""

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    "bb_period": 14,
    "bb_std": 2.0,
    "rsi_period": 10,
    "rsi_low": 30,
    "rsi_high": 70,
    "vol_target": 0.012,
    "ema_trend_period": 200,
    "counter_trend_size": 0.25,
}

PARAM_SPACE: dict = {
    "bb_period": (10, 20),
    "rsi_period": (7, 14),
    "ema_trend_period": (100, 400),
    "counter_trend_size": (0.0, 0.7),
}


def _calc_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _signals_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"]
    high, low = df["high"], df["low"]

    bb_p = int(params.get("bb_period", 14))
    bb_s = float(params.get("bb_std", 2.0))
    rsi_p = int(params.get("rsi_period", 10))
    rsi_l = float(params.get("rsi_low", 30))
    rsi_h = float(params.get("rsi_high", 70))

    # 1. Indicators
    sma = close.rolling(window=bb_p).mean()
    std = close.rolling(window=bb_p).std()
    upper_band = sma + (std * bb_s)
    lower_band = sma - (std * bb_s)
    rsi = _calc_rsi(close, rsi_p)

    # 2. Stateful Signals
    entry_long = (close < lower_band) & (rsi < rsi_l)
    entry_short = (close > upper_band) & (rsi > rsi_h)

    exit_long = (close > sma) | (rsi > 50)
    exit_short = (close < sma) | (rsi < 50)

    pos_long = pd.Series(np.nan, index=df.index)
    pos_long.loc[entry_long] = 1.0
    pos_long.loc[exit_long] = 0.0
    pos_long = pos_long.ffill().fillna(0.0)

    pos_short = pd.Series(np.nan, index=df.index)
    pos_short.loc[entry_short] = -1.0
    pos_short.loc[exit_short] = 0.0
    pos_short = pos_short.ffill().fillna(0.0)

    pos = np.where(pos_long != 0, pos_long, pos_short)
    pos = pd.Series(pos, index=df.index)

    # 3. Sizing
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
    atr_pct = (atr / close).clip(lower=0.001)
    vol_target = float(params.get("vol_target", 0.012))
    size_mult = (vol_target / atr_pct).clip(lower=0.3, upper=1.0)

    # 3b. Counter-trend size dampener
    ema_p = int(params.get("ema_trend_period", 200))
    ema_trend = close.ewm(span=ema_p, adjust=False).mean()
    uptrend = (close > ema_trend).astype(float)
    downtrend = (close < ema_trend).astype(float)
    ct_size = float(params.get("counter_trend_size", 0.25))
    regime_mult = pd.Series(1.0, index=df.index)
    regime_mult = regime_mult.where(~((pos > 0) & (downtrend > 0)), ct_size)
    regime_mult = regime_mult.where(~((pos < 0) & (uptrend > 0)), ct_size)

    # 3c. ATR stop-loss tracker — exit when running loss exceeds 2.5*ATR from entry
    # Compute trailing entry price by tracking position changes
    pos_change = pos.diff().fillna(pos)
    is_entry = (pos != 0) & ((pos.shift(1).fillna(0) == 0) | (np.sign(pos) != np.sign(pos.shift(1).fillna(0))))
    entry_price = close.where(is_entry).ffill()
    atr_at_entry = atr.where(is_entry).ffill()
    # Loss vs entry (positive = adverse move)
    adverse = (entry_price - close) * np.sign(pos)  # positive = bad
    stop_hit = adverse > (2.5 * atr_at_entry)
    pos_stopped = pos.where(~stop_hit, 0.0)

    return (pos_stopped * size_mult * regime_mult).shift(1).fillna(0.0)


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
