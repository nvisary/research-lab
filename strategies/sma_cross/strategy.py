"""SMA(20/100) crossover, vol-targeted, with entry-cooldown after flip."""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS = ["BTCUSDT"]
DEFAULT_TF = "4h"

DEFAULT_PARAMS = {
    "fast": 20,
    "slow": 100,
    "vol_lookback": 21,     # ~3.5 days of 4h bars: fast vol response
    "target_daily_vol": 0.02,
    "cooldown_bars": 0,
    "bars_per_day": 6,      # 24h / 4h
}

PARAM_SPACE = {
    "fast": (5, 100),
    "slow": (20, 500),
    "vol_lookback": (24, 720),
    "target_daily_vol": (0.005, 0.05),
    "cooldown_bars": (0, 48),
}


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    fast = int(params["fast"])
    slow = int(params["slow"])
    vol_lookback = int(params["vol_lookback"])
    target_daily_vol = float(params["target_daily_vol"])
    cooldown_bars = int(params["cooldown_bars"])
    if fast >= slow:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    rows = []
    for sym, df in data.items():
        if df.empty or len(df) < max(slow, vol_lookback) + 5:
            continue
        close = df["close"]
        sma_fast = close.rolling(fast).mean()
        sma_slow = close.rolling(slow).mean()
        # Long-only at 4h: BTC has structural long bias; shorts pay funding+drift
        direction = (sma_fast > sma_slow).astype(float)  # 0 or 1

        # Cooldown: take new direction only if it's been the same for >= cooldown_bars
        # i.e., reject 'flicker' flips. Implemented as: a flip is committed only after
        # `cooldown_bars` consecutive same-sign signals.
        if cooldown_bars > 0:
            same = direction.eq(direction.shift(1))
            run = same.groupby((~same).cumsum()).cumcount() + 1
            committed = direction.where(run >= cooldown_bars)
            committed = committed.ffill().fillna(0.0)
        else:
            committed = direction

        ret = close.pct_change()
        realized_daily_vol = ret.rolling(vol_lookback).std() * np.sqrt(int(params.get("bars_per_day", 24)))
        size = (target_daily_vol / realized_daily_vol).clip(upper=1.0).fillna(0.0)

        pos = (committed * size).clip(-1.0, 1.0)
        pos = pos.shift(1).fillna(0.0)

        rows.append(pd.DataFrame({
            "timestamp": df.index, "symbol": sym, "position": pos.values,
        }))

    if not rows:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(rows, ignore_index=True)
