"""Funding-rate mean reversion on crowded alt-perp extremes.

Thesis: when funding is extreme and price closes outside a 4h Bollinger band,
the perp is crowded enough that carrying the consensus trade becomes expensive.
The baseline fades that crowding and exits when funding normalizes or the
fixed reward/risk bracket resolves.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from datafeed.loader import load_funding


DEFAULT_SYMBOLS: list[str] = [
    "1000PEPEUSDT",
    "AAVEUSDT",
    "ADAUSDT",
    "ALGOUSDT",
    "ARBUSDT",
    "AVAXUSDT",
    "BCHUSDT",
    "BNBUSDT",
    "DASHUSDT",
    "DOGEUSDT",
    "DOTUSDT",
    "ENJUSDT",
    "HBARUSDT",
    "INJUSDT",
    "JTOUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "NEARUSDT",
    "OPUSDT",
    "SOLUSDT",
    "SUIUSDT",
    "TIAUSDT",
    "TONUSDT",
    "TRXUSDT",
    "UNIUSDT",
    "WLDUSDT",
    "XLMUSDT",
    "XMRUSDT",
    "XRPUSDT",
    "ZECUSDT",
]

DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "funding_entry": 0.00075,     # +/- 0.075% per 8h funding print
    "fallback_funding_entry": 0.0005,
    "persistence_funding": 0.00035,
    "persistence_bars": 6,
    "confirm_ret_bars": 6,        # 24h on 4h bars
    "volume_window": 45,          # 7.5 days on 4h bars
    "funding_exit": 0.0,          # disable funding-normalization exit
    "bb_period": 20,
    "bb_k": 2.0,
    "atr_period": 14,
    "stop_atr": 1.5,
    "reward_risk": 2.5,
    "max_hold_bars": 6,          # 1 day on 4h bars
}

PARAM_SPACE: dict = {
    "funding_entry": (0.0005, 0.0020),
    "fallback_funding_entry": (0.0003, 0.0010),
    "persistence_funding": (0.0002, 0.0007),
    "persistence_bars": (3, 12),
    "confirm_ret_bars": (3, 12),
    "volume_window": (20, 120),
    "funding_exit": (0.0, 0.0003),
    "bb_period": (10, 60),
    "bb_k": (1.5, 3.0),
    "atr_period": (7, 40),
    "stop_atr": (0.8, 3.0),
    "reward_risk": (1.5, 4.0),
    "max_hold_bars": (6, 42),
}

RAW_SIZING = True


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _funding_rate(symbol: str, index: pd.DatetimeIndex) -> pd.Series:
    funding = load_funding(symbol, index[0], index[-1] + pd.Timedelta("4h"))
    if funding.empty:
        return pd.Series(0.0, index=index)
    return funding["rate"].reindex(index, method="ffill").fillna(0.0)


def _positions_for_symbol(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
    n_universe: int,
) -> pd.Series:
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    funding_entry = float(params.get("funding_entry", 0.0010))
    fallback_funding_entry = float(params.get("fallback_funding_entry", 0.0005))
    persistence_funding = float(params.get("persistence_funding", 0.00035))
    persistence_bars = int(params.get("persistence_bars", 6))
    confirm_ret_bars = int(params.get("confirm_ret_bars", 6))
    volume_window = int(params.get("volume_window", 45))
    funding_exit = float(params.get("funding_exit", 0.0001))
    bb_period = int(params.get("bb_period", 20))
    bb_k = float(params.get("bb_k", 2.0))
    atr_period = int(params.get("atr_period", 14))
    stop_atr = float(params.get("stop_atr", 1.5))
    reward_risk = float(params.get("reward_risk", 2.5))
    max_hold_bars = int(params.get("max_hold_bars", 18))

    mid = close.rolling(bb_period, min_periods=bb_period).mean()
    std = close.rolling(bb_period, min_periods=bb_period).std()
    upper = mid + bb_k * std
    lower = mid - bb_k * std
    atr = _atr(df, atr_period)
    funding = _funding_rate(symbol, df.index)
    ret_confirm = close.pct_change(confirm_ret_bars)
    volume_ok = df["volume"].astype(float) > df["volume"].astype(float).rolling(
        volume_window,
        min_periods=max(6, volume_window // 3),
    ).median()

    strong_long = funding >= funding_entry
    strong_short = funding <= -funding_entry
    fallback_long = (
        (funding >= fallback_funding_entry)
        & (funding < funding_entry)
        & (ret_confirm > 0)
        & volume_ok
    )
    fallback_short = (
        (funding <= -fallback_funding_entry)
        & (funding > -funding_entry)
        & (ret_confirm < 0)
        & volume_ok
    )
    persistent_long = (
        (funding >= persistence_funding)
        & (funding.rolling(persistence_bars, min_periods=persistence_bars).min() >= persistence_funding)
        & (ret_confirm > 0)
    )
    persistent_short = (
        (funding <= -persistence_funding)
        & (funding.rolling(persistence_bars, min_periods=persistence_bars).max() <= -persistence_funding)
        & (ret_confirm < 0)
    )

    long_entry = strong_long | fallback_long | persistent_long
    short_entry = strong_short | fallback_short | persistent_short

    closes = close.to_numpy()
    highs = high.to_numpy()
    lows = low.to_numpy()
    atrs = atr.to_numpy()
    fund = funding.to_numpy()
    le = long_entry.fillna(False).to_numpy()
    se = short_entry.fillna(False).to_numpy()

    out = np.zeros(len(df), dtype=float)
    state = 0.0
    entry = np.nan
    stop = np.nan
    target = np.nan
    bars_held = 0

    for i in range(len(df)):
        c = closes[i]
        a = atrs[i]
        if not np.isfinite(c) or not np.isfinite(a):
            out[i] = state
            continue

        if state == 0.0:
            if le[i]:
                state = 1.0
                entry = c
                risk = stop_atr * a
                stop = entry - risk
                target = entry + reward_risk * risk
                bars_held = 0
            elif se[i]:
                state = -1.0
                entry = c
                risk = stop_atr * a
                stop = entry + risk
                target = entry - reward_risk * risk
                bars_held = 0
        else:
            bars_held += 1
            funding_normalized = abs(fund[i]) <= funding_exit
            timed_out = bars_held >= max_hold_bars
            if state > 0:
                bracket_done = lows[i] <= stop or highs[i] >= target
            else:
                bracket_done = highs[i] >= stop or lows[i] <= target
            if funding_normalized or bracket_done or timed_out:
                state = 0.0
                entry = np.nan
                stop = np.nan
                target = np.nan
                bars_held = 0
        out[i] = state

    pos = pd.Series(out, index=df.index) / max(n_universe, 1)
    return pos.shift(1).fillna(0.0)


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    n = sum(1 for df in data.values() if df is not None and not df.empty)
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _positions_for_symbol(symbol, df, params, n_universe=n)
        frames.append(
            pd.DataFrame(
                {"timestamp": df.index, "symbol": symbol, "position": pos.values}
            )
        )
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
