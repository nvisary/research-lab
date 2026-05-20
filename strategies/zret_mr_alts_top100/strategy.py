"""zret_mr_alts_top100 — return-z MR on top-100 perp basket.

Identical signal architecture and best params from `zret_mr_alts` iter 11.
ONE structural change: universe expanded from 24 mid-cap alts (excluding
top-5 by mcap) to top-100 by quote volume over 2024-2025.

Universe rationale:
- More symbols → more parallel trades → tighter Sharpe estimates per window
  → BHY haircut shrinks (more statistical power per trial)
- Includes BTC/ETH/SOL/BNB/XRP this time (top-5 by mcap that were excluded
  in zret_mr_alts). User explicitly asked for top-100, not "not-top". They
  may genuinely behave MR differently than mid-caps; let the data say.
- Survivorship bias: same caveat as the smaller universe — these are
  currently-listed. Delisted alts from 2024-2026 missing.

The universe is committed to `universe.json` so it's reproducible
without re-running the volume ranker.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from harness.utils import resample_higher


_HERE = Path(__file__).resolve().parent
with (_HERE / "universe.json").open("r", encoding="utf-8") as _f:
    DEFAULT_SYMBOLS: list[str] = json.load(_f)


DEFAULT_TF: str = "15min"

# Inherited from zret_mr_alts iter 11 best (the only KEEP composite > 0).
DEFAULT_PARAMS: dict = {
    "z_window": 96,                 # 24h return + baseline window
    "entry_k": 2.0,
    "exit_k": 1.0,                  # partial-reversion exit
    "long_only": 0,
    "trend_ema": 50,
    "trend_slope_window": 5,
    "regime_lookback_days": 30,
    "regime_quantile": 0.2,         # tighter regime gate (was 0.3 in early iters)
    "vol_floor_q": 0.65,            # aggressive vol-floor
    "atr_period": 14,
}

PARAM_SPACE: dict = {
    "z_window": (24, 384),
    "entry_k": (1.0, 4.0),
    "exit_k": (0.0, 1.5),
    "long_only": (0, 1),
    "trend_ema": (20, 200),
    "trend_slope_window": (2, 20),
    "regime_lookback_days": (7, 90),
    "regime_quantile": (0.1, 0.8),
    "vol_floor_q": (0.0, 0.9),
    "atr_period": (7, 50),
}


def _positions_for_symbol(df: pd.DataFrame, params: dict, n_universe: int) -> pd.Series:
    close = df["close"]
    high, low = df["high"], df["low"]
    z_window = int(params.get("z_window", 96))
    entry_k = float(params.get("entry_k", 2.0))
    exit_k = float(params.get("exit_k", 1.0))
    long_only = int(params.get("long_only", 0)) == 1
    vol_floor_q = float(params.get("vol_floor_q", 0.65))
    atr_period = int(params.get("atr_period", 14))

    log_close = np.log(close)
    ret_n = log_close - log_close.shift(z_window)
    mean_r = ret_n.rolling(z_window, min_periods=z_window).mean()
    std_r = ret_n.rolling(z_window, min_periods=z_window).std()
    z = (ret_n - mean_r) / std_r

    trend_ema = int(params.get("trend_ema", 50))
    slope_window = int(params.get("trend_slope_window", 5))
    regime_lb_days = int(params.get("regime_lookback_days", 30))
    regime_q = float(params.get("regime_quantile", 0.2))

    df4h = resample_higher(df, "4h", {"close": "last"}, target_index=df.index)
    ema4h = df4h["close"].ewm(span=trend_ema, adjust=False, min_periods=trend_ema).mean()
    slope_4h = (ema4h - ema4h.shift(slope_window)) / ema4h
    abs_slope = slope_4h.abs()
    lb_bars = max(regime_lb_days * 96, 96)
    slope_thresh = abs_slope.rolling(lb_bars, min_periods=lb_bars).quantile(regime_q)
    flat_regime = (abs_slope <= slope_thresh).fillna(False)

    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=atr_period, adjust=False, min_periods=atr_period).mean()
    atr_pct = atr / close
    vol_thresh = atr_pct.rolling(lb_bars, min_periods=lb_bars).quantile(vol_floor_q)
    vol_ok = (atr_pct >= vol_thresh).fillna(False)

    long_entry = z < -entry_k
    short_entry = z > entry_k
    exit_zone = z.abs() < exit_k

    state = np.zeros(len(z), dtype=np.float64)
    cur = 0.0
    z_vals = z.to_numpy()
    le = long_entry.to_numpy()
    se = short_entry.to_numpy()
    ex = exit_zone.to_numpy()
    flat = flat_regime.to_numpy()
    vok = vol_ok.to_numpy()
    for i in range(len(z_vals)):
        if np.isnan(z_vals[i]):
            state[i] = 0.0
            cur = 0.0
            continue
        if cur == 0.0:
            if flat[i] and vok[i] and le[i]:
                cur = 1.0
            elif flat[i] and vok[i] and se[i] and not long_only:
                cur = -1.0
        else:
            if ex[i]:
                cur = 0.0
            elif flat[i] and vok[i] and cur > 0 and se[i] and not long_only:
                cur = -1.0
            elif flat[i] and vok[i] and cur < 0 and le[i]:
                cur = 1.0
        state[i] = cur

    pos = pd.Series(state, index=df.index)
    pos = pos / max(n_universe, 1)
    return pos.shift(1).fillna(0.0)


RAW_SIZING = True


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    n = sum(1 for df in data.values() if df is not None and not df.empty)
    frames = []
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        pos = _positions_for_symbol(df, params, n_universe=n)
        frames.append(pd.DataFrame({
            "timestamp": df.index,
            "symbol": symbol,
            "position": pos.values,
        }))
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
