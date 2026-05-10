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

DESCRIPTION = (
    "Time-series momentum (trend following). Per asset on 1d bars, "
    "position = sign of the 14-day trailing return — long if the asset "
    "has been up, short if down. Each of the 10 majors evaluated "
    "independently. Classic Moskowitz-Ooi-Pedersen TSM applied to "
    "crypto: fires when the WHOLE basket trends one direction."
)

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
]
DEFAULT_TF = "1d"

DEFAULT_PARAMS = {
    "lookback": 14,    # days of trailing return to sign
    "long_only": 0,
}

PARAM_SPACE = {
    "lookback": (3, 90),
    "long_only": (0, 1),
}


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    lookback = int(params.get("lookback", 14))
    long_only = bool(int(params.get("long_only", 0)))
    if lookback < 2:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    rows: list[pd.DataFrame] = []
    for sym, df in data.items():
        if df.empty or len(df) < lookback + 2:
            continue
        close = df["close"]
        ret = close.pct_change(lookback)
        direction = np.sign(ret).fillna(0.0)
        if long_only:
            direction = direction.clip(lower=0.0)
        # No-lookahead: ret[t] uses close[t]; position[t] should
        # depend only on info up to bar t-1. shift(1) on the signal.
        pos = direction.shift(1).fillna(0.0)
        rows.append(pd.DataFrame({
            "timestamp": df.index, "symbol": sym, "position": pos.values,
        }))

    if not rows:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(rows, ignore_index=True)
