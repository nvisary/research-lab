"""Time-series momentum: per-asset trend following.

Per symbol on 1d bars, position = sign of the N-day trailing return.
Each asset trades independently; basket diversification comes from
mixing assets in different micro-regimes (e.g. BTC trending, alts
chopping). Classic Moskowitz/Ooi/Pedersen TSM, applied to crypto majors.

Not adjusted for vol — equal-weight across symbols in the harness
sizing path. Holds while signal is positive, flips on signal change.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
]
DEFAULT_TF = "1d"

DEFAULT_PARAMS = {
    "lookback": 14,         # days of trailing return for LONG signal
    "short_lookback": 30,   # days of trailing return for SHORT signal (0 = same as lookback)
    "long_only": 0,
    "ema_smooth": 3,        # smooth close with N-day EMA before computing momentum (1 = raw)
}

PARAM_SPACE = {
    "lookback": (3, 90),
    "short_lookback": (0, 200),
    "long_only": (0, 1),
    "ema_smooth": (1, 20),
}


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    lookback = int(params.get("lookback", 14))
    short_lookback = int(params.get("short_lookback", 0)) or lookback
    long_only = bool(int(params.get("long_only", 0)))
    ema_smooth = int(params.get("ema_smooth", 1))
    if lookback < 2 or short_lookback < 2:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    rows: list[pd.DataFrame] = []
    for sym, df in data.items():
        if df.empty or len(df) < max(lookback, short_lookback) + 2:
            continue
        close = df["close"]
        # Optional EMA smoothing on close to dampen daily noise before
        # computing N-day return. Adds slight lag in exchange for fewer
        # whipsaws around the sign boundary.
        if ema_smooth >= 2:
            sm = close.ewm(span=ema_smooth, adjust=False, min_periods=ema_smooth).mean()
        else:
            sm = close
        long_ret = sm.pct_change(lookback)
        short_ret = sm.pct_change(short_lookback)
        # Asymmetric signal: enter long on fast-lookback positive return,
        # enter short on slow-lookback negative return (require deeper
        # confirmation to fade rallies that often bounce in crypto).
        direction = pd.Series(0.0, index=close.index)
        direction = direction.where(~(long_ret > 0), 1.0)
        if not long_only:
            direction = direction.where(~(short_ret < 0), -1.0)
        # No-lookahead: ret[t] uses close[t]; position[t] should
        # depend only on info up to bar t-1. shift(1) on the signal.
        pos = direction.shift(1).fillna(0.0)
        rows.append(pd.DataFrame({
            "timestamp": df.index, "symbol": sym, "position": pos.values,
        }))

    if not rows:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(rows, ignore_index=True)
