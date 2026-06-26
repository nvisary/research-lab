"""Cross-sectional pump-fade — ported from notebooks/ manual research.

Hypothesis (built and validated in notebooks/pump/01-10 across 2024 H2, 2025 and
2026, pooled over many symbols): after a sharp pump — cumulative +5% over 15
minutes AND 15-minute volume > 3x the rolling-median minute volume — price
tends to FADE. The mean path peaks AT the trigger and mean-reverts ~1.5-2%
over the next few hours. Trade: SHORT at the trigger, 3% stop, 4h time-exit.
Pooled net edge ~1.5%/trade (after fees+funding), win ~56%, stable across
three independent periods. Funding is a small drag; the tail risk is the
short-vol nature (rare runners), bounded by a 3% stop.

This file ports that EXACT rule into the harness contract, to cross-check the
hand-rolled notebook backtest against the repo's independent engine. We do not
re-tune here — params mirror the notebook study.

Execution model: signal is computed on bars up to t, then the whole position
series is shifted by one bar (lookahead-safe), i.e. we act on the bar AFTER the
trigger. The 3% stop is close-based, matching the notebook sim; the final shift
makes the stop reaction depend only on prior closes (passes the audit).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# Top-40 pumpiest perps (by raw-trigger count over 2024-01..2025-09) with full
# coverage on that window. This is the regime where the edge lives — liquid
# majors almost never do +5%/15m, so they were dropped. Memecoins included where
# data spans the window (1000PEPE/BONK/BTT end 2025-09 -> run the harness with
# --end 2025-09-01).
DEFAULT_SYMBOLS: list[str] = [
    "XCNUSDT", "PEOPLEUSDT", "OGUSDT", "XVGUSDT", "SNTUSDT", "GODSUSDT",
    "USTCUSDT", "CKBUSDT", "LPTUSDT", "COREUSDT", "WAVESUSDT", "UMAUSDT",
    "GLMUSDT", "1000PEPEUSDT", "API3USDT", "1000BONKUSDT", "SLPUSDT",
    "ARKUSDT", "ENSUSDT", "ARPAUSDT", "CVXUSDT", "JASMYUSDT", "CROUSDT",
    "MEMEUSDT", "BIGTIMEUSDT", "CVCUSDT", "FLRUSDT", "CTCUSDT", "NMRUSDT",
    "TRBUSDT", "REQUSDT", "AUCTIONUSDT", "SCRTUSDT", "OGNUSDT", "RPLUSDT",
    "ZENUSDT", "RLCUSDT", "RSRUSDT", "GASUSDT", "1000BTTUSDT",
]

DEFAULT_TF: str = "1min"

DEFAULT_PARAMS: dict = {
    "ret_window": 15,         # minutes for the cumulative pump return
    "ret_threshold": 0.05,    # +5% over ret_window
    "vol_window": 15,         # minutes for the volume-surge sum
    "vol_med_window": 240,    # rolling window for the typical minute volume
    "vol_med_min": 60,        # min periods for the median
    "surge_threshold": 3.0,   # volume > 3x typical
    "hold_bars": 240,         # 4h time-exit
    "stop_pct": 0.03,         # 3% stop (close-based)
    "cooldown_bars": 120,     # one event per ~2h per symbol
    "weight": 0.02,           # 2% of equity per short (matches $20/$1000)
}

PARAM_SPACE: dict = {
    "ret_threshold": (0.02, 0.12),
    "surge_threshold": (2.0, 8.0),
    "hold_bars": (30, 480),
    "stop_pct": (0.01, 0.10),
    "cooldown_bars": (30, 240),
    "weight": (0.005, 0.05),
}

# Absolute-equity-fraction sizing (a pump short is `weight` of equity, not a slot).
RAW_SIZING = True
MAX_POSITION = 0.05


def _positions_for_symbol(df: pd.DataFrame, params: dict) -> pd.Series:
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    rw = int(params["ret_window"])
    vw = int(params["vol_window"])
    vmw = int(params["vol_med_window"])
    vmm = int(params["vol_med_min"])
    ret_th = float(params["ret_threshold"])
    surge_th = float(params["surge_threshold"])
    hold = max(1, int(params["hold_bars"]))
    stop = float(params["stop_pct"])
    cooldown = max(0, int(params["cooldown_bars"]))
    w = float(params["weight"])

    ret = close.pct_change(rw)
    vol_med = volume.rolling(vmw, min_periods=vmm).median()
    surge = volume.rolling(vw).sum() / (vol_med * vw)
    trig = ((ret > ret_th) & (surge > surge_th)).fillna(False).to_numpy()

    c = close.to_numpy()
    n = len(c)
    pos = np.zeros(n, dtype=np.float64)
    next_allowed = 0
    for e in np.flatnonzero(trig):
        if e < next_allowed or e + 1 >= n:
            continue
        p0 = c[e]
        thr = p0 * (1.0 + stop)
        end = min(n, e + hold)
        ex = end
        for k in range(e + 1, end):
            if c[k] >= thr:          # close-based stop
                ex = k
                break
        pos[e:ex] = -w               # short from the trigger bar (pre-shift)
        next_allowed = max(ex, e + cooldown)

    # one-bar execution lag -> lookahead-safe (decision uses only prior closes)
    return pd.Series(pos, index=df.index).shift(1).fillna(0.0)


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _positions_for_symbol(df, params)
        frames.append(pd.DataFrame({
            "timestamp": df.index,
            "symbol": symbol,
            "position": pos.values,
        }))
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
