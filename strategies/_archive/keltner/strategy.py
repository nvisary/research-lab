"""Keltner Channels — breakout baseline.

Keltner Channels are ATR-based volatility bands centred on an EMA:
    middle[t] = EMA(close, ema_period)[t]
    upper[t]  = middle[t] + multiplier * ATR(atr_period)[t]
    lower[t]  = middle[t] - multiplier * ATR(atr_period)[t]

Two classic uses:
  1. Breakout / trend-following — long when close > upper, short when
     close < lower. This file's baseline.
  2. Mean reversion — buy at lower, sell at middle/upper. Try as a
     hypothesis variant.

Compared to Bollinger Bands (σ-based), Keltner uses ATR which adapts
to recent realised range without compressing on low-σ regimes (small
σ ≠ small ATR). The expectation: bands stay informative through both
calm and volatile regimes.

Position emission:
    if close > upper: pos = +1
    elif close < lower: pos = -1   (or 0 if long_only)
    else:              pos = 0     (out of market between bands)
    pos = pos.shift(1)              (no lookahead)

Out-of-band is "in trade", inside band is "flat". This is intentionally
simpler than supertrend's state-machine (which carries trend through
the band): Keltner here just answers "is the price currently outside
its volatility envelope?". Filter ideas (ADX, regime, multi-TF) layer
on top.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
]
DEFAULT_TF = "4h"

DEFAULT_PARAMS = {
    "ema_period": 20,
    "atr_period": 10,
    "multiplier": 2.0,
    "long_only": 0,        # 0 = long+short, 1 = long-only
    "trend_ma": 0,         # SMA period for regime filter (0 disables)
    "adx_period": 14,
    "adx_threshold": 0.0,  # require ADX > threshold; 0 disables
    "htf_ema_period": 50,  # higher-TF EMA span on 1d (0 disables HTF gate)
}

PARAM_SPACE = {
    "ema_period": (5, 100),
    "atr_period": (5, 50),
    "multiplier": (1.0, 4.0),
    "long_only": (0, 1),
    "trend_ma": (0, 400),
    "adx_period": (5, 30),
    "adx_threshold": (0.0, 40.0),
    "htf_ema_period": (0, 100),
}


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int) -> pd.Series:
    """Average True Range, Wilder smoothing (EMA with alpha = 1/period)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _wilder_adx(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int) -> pd.Series:
    """Average Directional Index (Wilder)."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False,
                                  min_periods=period).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False,
                                    min_periods=period).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    return adx


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    ema_period = int(params.get("ema_period", 20))
    atr_period = int(params.get("atr_period", 10))
    multiplier = float(params.get("multiplier", 2.0))
    long_only = bool(int(params.get("long_only", 0)))
    trend_ma = int(params.get("trend_ma", 0))
    adx_period = int(params.get("adx_period", 14))
    adx_threshold = float(params.get("adx_threshold", 0.0))
    htf_ema_period = int(params.get("htf_ema_period", 0))
    if ema_period < 2 or atr_period < 2 or multiplier <= 0:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    rows: list[pd.DataFrame] = []
    for sym, df in data.items():
        if df.empty or len(df) < max(ema_period, atr_period) + 5:
            continue
        close = df["close"]
        middle = close.ewm(span=ema_period, adjust=False,
                           min_periods=ema_period).mean()
        atr = _wilder_atr(df["high"], df["low"], close, atr_period)
        upper = middle + multiplier * atr
        lower = middle - multiplier * atr

        # Breakout direction: +1 above upper, -1 below lower, 0 between.
        direction = pd.Series(0.0, index=df.index)
        direction = direction.where(~(close > upper), 1.0)
        direction = direction.where(~(close < lower), -1.0)
        if long_only:
            direction = direction.clip(lower=0.0)

        # Trend regime filter: longs only when close > SMA(trend_ma),
        # shorts only when close < SMA(trend_ma). Mutes whipsaws when
        # the breakout direction fights the macro trend.
        if trend_ma >= 2:
            sma_trend = close.rolling(trend_ma, min_periods=trend_ma).mean()
            allow_long = (close > sma_trend)
            allow_short = (close < sma_trend)
            direction = direction.where(
                ~((direction > 0) & ~allow_long), 0.0)
            direction = direction.where(
                ~((direction < 0) & ~allow_short), 0.0)

        # ADX gate: only emit a position when the directional index
        # exceeds the threshold (genuine trend) — skip chop and
        # post-trend exhaustion where breakouts whipsaw.
        if adx_period >= 2 and adx_threshold > 0.0:
            adx = _wilder_adx(df["high"], df["low"], close, adx_period)
            direction = direction.where(adx > adx_threshold, 0.0)

        # Higher-TF (1d) trend gate: require 1d close to be above its
        # EMA(htf_ema_period) for longs, below it for shorts. resample_higher
        # applies the safe one-bar shift on the 1d series so the value at
        # decision-time t comes from the previous COMPLETED 1d bar.
        if htf_ema_period >= 2:
            df_1d = resample_higher(
                df,
                "1D",
                {"close": "last"},
                target_index=df.index,
            )
            htf_close = df_1d["close"].ffill()
            htf_ema = htf_close.ewm(span=htf_ema_period, adjust=False,
                                    min_periods=htf_ema_period).mean()
            htf_long_ok = htf_close > htf_ema
            htf_short_ok = htf_close < htf_ema
            direction = direction.where(
                ~((direction > 0) & ~htf_long_ok), 0.0)
            direction = direction.where(
                ~((direction < 0) & ~htf_short_ok), 0.0)

        # No-lookahead: position at bar t is direction computed up to t-1.
        pos = direction.shift(1).fillna(0.0)

        rows.append(pd.DataFrame({
            "timestamp": df.index, "symbol": sym, "position": pos.values,
        }))

    if not rows:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(rows, ignore_index=True)
