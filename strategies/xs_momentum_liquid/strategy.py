"""Cross-sectional momentum baseline on liquid USDT perps.

Hypothesis: medium-term relative strength persists across the liquid crypto
perp universe. Every 4h, rank symbols by volatility-normalized 7-day return;
hold the strongest names long and weakest names short with equal gross capital.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_SYMBOLS: list[str] = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "BNBUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "TRXUSDT",
    "BCHUSDT",
    "NEARUSDT",
    "ATOMUSDT",
    "LTCUSDT",
    "OPUSDT",
    "INJUSDT",
    "FILUSDT",
    "UNIUSDT",
    "SUIUSDT",
    "ICPUSDT",
]

DEFAULT_TF: str = "4h"

DEFAULT_PARAMS: dict = {
    "momentum_bars": 42,      # 7 days on 4h bars
    "vol_window": 42,
    "regime_window": 252,     # 6 weeks on 4h bars
    "vol_ceiling_q": 0.90,
    "top_k": 4,
    "rebalance_bars": 6,     # rebalance once per day
}

PARAM_SPACE: dict = {
    "momentum_bars": (18, 126),
    "vol_window": (18, 126),
    "regime_window": (126, 504),
    "vol_ceiling_q": (0.80, 0.98),
    "top_k": (2, 8),
    "rebalance_bars": (1, 12),
}

RAW_SIZING = True
MAX_POSITION = 0.25


def _score_frame(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    frames = {sym: df["close"] for sym, df in data.items() if df is not None and not df.empty}
    if not frames:
        return pd.DataFrame()
    closes = pd.concat(
        frames,
        axis=1,
    ).sort_index()
    if closes.empty:
        return closes

    momentum_bars = int(params.get("momentum_bars", 42))
    vol_window = int(params.get("vol_window", 42))

    log_close = np.log(closes)
    ret = log_close.diff()
    momentum = log_close.diff(momentum_bars)
    vol = ret.rolling(vol_window, min_periods=vol_window).std()
    score = momentum / (vol * np.sqrt(momentum_bars))
    market_vol = vol.median(axis=1)
    regime_window = int(params.get("regime_window", 252))
    vol_ceiling_q = float(params.get("vol_ceiling_q", 0.90))
    vol_ceiling = market_vol.rolling(
        regime_window,
        min_periods=regime_window,
    ).quantile(vol_ceiling_q)
    score = score.where(market_vol <= vol_ceiling)
    return score.replace([np.inf, -np.inf], np.nan)


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    score = _score_frame(data, params)
    if score.empty:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    top_k = max(1, int(params.get("top_k", 4)))
    rebalance_bars = max(1, int(params.get("rebalance_bars", 6)))

    positions = pd.DataFrame(0.0, index=score.index, columns=score.columns)
    signal_score = score.shift(1)
    current = pd.Series(0.0, index=score.columns)

    for i in range(0, len(signal_score), rebalance_bars):
        row = signal_score.iloc[i].dropna()
        current.loc[:] = 0.0
        if len(row) >= 2 * top_k:
            ranked = row.sort_values()
            shorts = ranked.index[:top_k]
            longs = ranked.index[-top_k:]
            leg = 0.5 / top_k
            current.loc[longs] = leg
            current.loc[shorts] = -leg
        end = min(i + rebalance_bars, len(signal_score))
        positions.iloc[i:end] = current.values

    stacked = positions.stack().rename("position").reset_index()
    stacked.columns = ["timestamp", "symbol", "position"]
    return stacked
