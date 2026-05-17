"""Built-in features. Add new ones here following the @register pattern.

LOOKAHEAD HYGIENE (read before adding):
  Every value at index ``t`` must depend ONLY on bars with timestamp ≤ t.
  Rolling/EWM windows in pandas are right-aligned by default, which is
  correct. Do NOT use ``center=True`` or any future-touching transform.
  Tests in tests/test_features.py verify each feature by tail-poisoning
  the input — if any output before the poisoned region changes, you
  have a leak.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from datafeed.loader import load, load_funding
from features.registry import register


# --------------------------------------------------------------------------- #
def _load_ohlcv(symbol: str, start, end, tf: str) -> pd.DataFrame:
    return load(symbol, start, end, tf=tf)


# --------------------------------------------------------------------------- #
@register(
    "atr_14",
    description="14-bar Average True Range (Wilder). Volatility in price units.",
    deps=["ohlcv"], lookback="3D",
)
def atr_14(symbol, start, end, tf):
    df = _load_ohlcv(symbol, start, end, tf)
    if df.empty:
        return pd.Series(dtype="float64", name="atr_14")
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    # Wilder smoothing = EWMA with alpha = 1/14.
    return tr.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()


@register(
    "atr_pct_14",
    description="ATR-14 as fraction of close — scale-invariant volatility proxy.",
    deps=["ohlcv"], lookback="3D",
)
def atr_pct_14(symbol, start, end, tf):
    df = _load_ohlcv(symbol, start, end, tf)
    if df.empty:
        return pd.Series(dtype="float64", name="atr_pct_14")
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
    return atr / df["close"]


# --------------------------------------------------------------------------- #
@register(
    "realized_vol_30",
    description="Rolling 30-bar std of log returns. NOT annualized.",
    deps=["ohlcv"], lookback="5D",
)
def realized_vol_30(symbol, start, end, tf):
    df = _load_ohlcv(symbol, start, end, tf)
    if df.empty:
        return pd.Series(dtype="float64", name="realized_vol_30")
    log_ret = np.log(df["close"]).diff()
    return log_ret.rolling(30, min_periods=30).std(ddof=1)


@register(
    "ret_24h",
    description="Trailing 24h log-return. Units: log price ratio.",
    deps=["ohlcv"], lookback="2D",
)
def ret_24h(symbol, start, end, tf):
    df = _load_ohlcv(symbol, start, end, tf)
    if df.empty:
        return pd.Series(dtype="float64", name="ret_24h")
    # Determine bars per 24h from index spacing — robust to tf string.
    if len(df) < 2:
        return pd.Series(dtype="float64", name="ret_24h")
    bar_seconds = (df.index[1] - df.index[0]).total_seconds()
    bars_per_day = max(1, int(round(86400.0 / bar_seconds)))
    return (np.log(df["close"]) - np.log(df["close"].shift(bars_per_day)))


# --------------------------------------------------------------------------- #
@register(
    "trend_50_200",
    description="Sign of (EMA50 − EMA200) / close. +1 long-term uptrend, −1 down.",
    deps=["ohlcv"], lookback="30D",
)
def trend_50_200(symbol, start, end, tf):
    df = _load_ohlcv(symbol, start, end, tf)
    if df.empty:
        return pd.Series(dtype="float64", name="trend_50_200")
    ema50 = df["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    ema200 = df["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    diff = (ema50 - ema200) / df["close"]
    return np.sign(diff).where(diff.notna())


# --------------------------------------------------------------------------- #
@register(
    "regime_class",
    description=(
        "4-bucket regime label: 2 × (vol low/high) × (trend up/down). "
        "Values 0..3 = (low-vol/up), (low-vol/down), (high-vol/up), (high-vol/down). "
        "Cut-points are rolling 250-bar medians — fully causal."
    ),
    deps=["ohlcv"], lookback="40D",
)
def regime_class(symbol, start, end, tf):
    df = _load_ohlcv(symbol, start, end, tf)
    if df.empty:
        return pd.Series(dtype="Int64", name="regime_class")
    log_ret = np.log(df["close"]).diff()
    vol = log_ret.rolling(30, min_periods=30).std(ddof=1)
    vol_median = vol.rolling(250, min_periods=50).median()
    high_vol = (vol > vol_median).astype("Int64")
    ema50 = df["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    ema200 = df["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    up = (ema50 > ema200).astype("Int64")
    # Bucket index: 2*vol_bit + (1 - up_bit). 0=lowvol-up, 1=lowvol-down,
    # 2=highvol-up, 3=highvol-down.
    out = (2 * high_vol + (1 - up)).astype("Int64")
    out = out.where(vol_median.notna() & ema200.notna())
    return out.rename("regime_class")


# --------------------------------------------------------------------------- #
@register(
    "funding_z_30",
    description=(
        "Bybit perp funding rate z-scored over its trailing 30-window mean/std "
        "(funding cadence is 8h, so this is ~10 days). Reindexed to bar TF "
        "with forward-fill so it's stale-safe for non-funding bars."
    ),
    deps=["funding"], lookback="20D",
)
def funding_z_30(symbol, start, end, tf):
    fdf = load_funding(symbol, start, end)
    if fdf.empty:
        return pd.Series(dtype="float64", name="funding_z_30")
    rate = fdf["rate"].astype(float)
    mu = rate.rolling(30, min_periods=10).mean()
    sd = rate.rolling(30, min_periods=10).std(ddof=1)
    z = (rate - mu) / sd.where(sd > 0)
    # Reindex to OHLCV bar grid with forward-fill — funding is paid every
    # 8h but we want the LATEST KNOWN value as of each bar, which is the
    # last realised rate; this preserves causality because rate at 08:00
    # is publicly observed at 08:00.
    df_ohlcv = _load_ohlcv(symbol, start, end, tf)
    if df_ohlcv.empty:
        return z.rename("funding_z_30")
    out = z.reindex(df_ohlcv.index, method="ffill")
    return out.rename("funding_z_30")


@register(
    "funding_rate",
    description="Raw Bybit perp funding rate (per 8h), forward-filled to bar TF.",
    deps=["funding"], lookback="2D",
)
def funding_rate(symbol, start, end, tf):
    fdf = load_funding(symbol, start, end)
    if fdf.empty:
        return pd.Series(dtype="float64", name="funding_rate")
    rate = fdf["rate"].astype(float)
    df_ohlcv = _load_ohlcv(symbol, start, end, tf)
    if df_ohlcv.empty:
        return rate.rename("funding_rate")
    return rate.reindex(df_ohlcv.index, method="ffill").rename("funding_rate")


# --------------------------------------------------------------------------- #
@register(
    "rsi_14",
    description="14-bar Relative Strength Index, Wilder smoothing. Range 0..100.",
    deps=["ohlcv"], lookback="3D",
)
def rsi_14(symbol, start, end, tf):
    df = _load_ohlcv(symbol, start, end, tf)
    if df.empty:
        return pd.Series(dtype="float64", name="rsi_14")
    delta = df["close"].diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.where(avg_loss > 0)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # When avg_loss == 0 the formula yields NaN → set RSI = 100 (max bullish).
    rsi = rsi.where(avg_loss > 0, 100.0)
    return rsi
