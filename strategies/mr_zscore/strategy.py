"""Time-series mean-reversion via per-asset z-score.

Per symbol, compute z = (close - rolling_mean(zwindow)) / rolling_std(zwindow).
Enter long when z dips below -z_thresh, exit when z returns to z_exit.
Symmetrically for short. State machine per asset; positions across the
basket sum independently (10 majors, equal weight via harness sizing).

Hypothesis: when an asset's price is far from its own recent mean
(extreme in its OWN history), the next move is more likely to be
mean-reverting than continuing. Independent per-asset, so during
broad market trends only the laggard/leader extremes generate
signals — naturally orthogonal to cross-sectional rank-based MR.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
]
DEFAULT_TF = "1h"

DEFAULT_PARAMS = {
    "zwindow": 168,    # rolling window for mean/std (1 week of 1h bars)
    "z_thresh": 2.0,   # |z| > z_thresh required to enter
    "z_exit": 0.0,     # exit when z crosses this level toward zero
    "long_only": 0,
}

PARAM_SPACE = {
    "zwindow": (24, 720),
    "z_thresh": (1.0, 4.0),
    "z_exit": (-1.0, 1.0),
    "long_only": (0, 1),
}


def _state_machine(z: np.ndarray, z_thresh: float, z_exit: float,
                   long_only: bool) -> np.ndarray:
    n = z.shape[0]
    out = np.zeros(n, dtype=np.float64)
    pos = 0
    for t in range(n):
        v = z[t]
        if not np.isfinite(v):
            out[t] = pos
            continue
        if pos > 0 and v >= z_exit:
            pos = 0
        elif pos < 0 and v <= -z_exit:
            pos = 0
        if pos == 0:
            if v <= -z_thresh:
                pos = 1
            elif (not long_only) and v >= z_thresh:
                pos = -1
        out[t] = pos
    return out


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    zwindow = int(params.get("zwindow", 168))
    z_thresh = float(params.get("z_thresh", 2.0))
    z_exit = float(params.get("z_exit", 0.0))
    long_only = bool(int(params.get("long_only", 0)))
    if zwindow < 5 or z_thresh <= 0:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    rows: list[pd.DataFrame] = []
    for sym, df in data.items():
        if df.empty or len(df) < zwindow + 5:
            continue
        close = df["close"]
        m = close.rolling(zwindow, min_periods=zwindow).mean()
        s = close.rolling(zwindow, min_periods=zwindow).std(ddof=0)
        z = (close - m) / s.replace(0, np.nan)
        direction = _state_machine(z.to_numpy(dtype=np.float64),
                                    z_thresh, z_exit, long_only)
        pos = pd.Series(direction, index=df.index).shift(1).fillna(0.0)
        rows.append(pd.DataFrame({
            "timestamp": df.index, "symbol": sym, "position": pos.values,
        }))

    if not rows:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(rows, ignore_index=True)
