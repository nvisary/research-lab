"""EMA crossover pilot — minimal strategy to bring up the harness.

Contract:
    DEFAULT_PARAMS / PARAM_SPACE / DEFAULT_SYMBOLS — read by the harness.
    generate_signals(data, params) -> long-format DataFrame.

The agent is allowed to edit ANYTHING in this file: defaults, param space,
indicators, even the logic — provided the contract holds.
"""
from __future__ import annotations

import pandas as pd

DEFAULT_SYMBOLS = ["BTCUSDT"]

# Decision frequency. Harness loads OHLCV at this TF and runs the backtest on
# it. For multi-TF strategies (e.g. 5m decisions confirmed by 30m and 4h
# trend), set DEFAULT_TF to the FINEST TF and use harness.utils.resample_higher
# to derive the higher-TF signals safely from `data` inside generate_signals.
DEFAULT_TF = "1h"

DEFAULT_PARAMS = {
    "fast": 12,
    "slow": 48,
    "vol_filter": 0.0,
}

PARAM_SPACE = {
    "fast": (4, 200),
    "slow": (10, 500),
    "vol_filter": (0.0, 0.05),
}


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    fast = int(params["fast"])
    slow = int(params["slow"])
    vol_filter = float(params.get("vol_filter", 0.0))
    if fast >= slow:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    rows = []
    for sym, df in data.items():
        if df.empty or len(df) < slow + 5:
            continue
        close = df["close"]
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()

        pos = (ema_fast > ema_slow).astype(float) * 2 - 1  # ±1 long/short

        if vol_filter > 0:
            ret = close.pct_change()
            vol = ret.rolling(slow).std()
            pos = pos.where(vol > vol_filter, 0.0)

        # avoid lookahead: act on the bar after the signal forms
        pos = pos.shift(1).fillna(0.0)

        out = pd.DataFrame({
            "timestamp": df.index,
            "symbol": sym,
            "position": pos.values,
        })
        rows.append(out)

    if not rows:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(rows, ignore_index=True)
