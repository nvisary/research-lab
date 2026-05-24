"""zscore_mr_alts — cross-sectional z-score mean reversion on mid-cap alt perps.

Baseline thesis: mid-cap alts (excluding BTC/ETH/SOL/BNB/XRP) chop harder than
the top tier and revert to a short-window mean on 15m more reliably than they
trend. Trade a basket so a single-symbol blow-up doesn't dominate.

Per symbol on 15m bars:
- z = (close - SMA(N)) / std(N), using N=`z_window` bars of prior closes only.
- enter LONG  when z < -entry_k  (oversold)
- enter SHORT when z >  entry_k  (overbought)
- exit when |z| < exit_k  (mean reached)
- state-machine: hold position between threshold crossings (one position per
  symbol at a time).

Sizing: cross-sectional equal-weight. Each symbol's slot is 1 / n_universe so
gross exposure is bounded by 100% when all symbols are simultaneously in.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher


# Mid-cap perp universe, excluding top-5 by market cap (BTC, ETH, SOL, BNB, XRP).
# All 24 names verified to have full 2024-01 → 2026-04 monthly coverage on disk.
DEFAULT_SYMBOLS: list[str] = [
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "TRXUSDT",
    "BCHUSDT", "NEARUSDT", "ATOMUSDT", "XLMUSDT", "OPUSDT",
    "INJUSDT", "SUIUSDT", "TIAUSDT", "SEIUSDT", "UNIUSDT",
    "FILUSDT", "HBARUSDT", "ICPUSDT", "LDOUSDT", "CRVUSDT",
    "SANDUSDT", "AXSUSDT", "IMXUSDT", "ETCUSDT",
]

DEFAULT_TF: str = "15min"

DEFAULT_PARAMS: dict = {
    "z_window": 96,     # 96 * 15m = 24h rolling baseline
    "entry_k": 2.0,     # |z| > 2 to enter
    "exit_k": 0.5,      # |z| < 0.5 to exit
    "long_only": 0,
    # 4h trend-regime gate: only fade in flat regime.
    "trend_ema": 50,                # 4h EMA span
    "trend_slope_window": 5,        # bars to measure slope (5 * 4h = 20h)
    "regime_lookback_days": 30,     # rolling window for slope quantile
    "regime_quantile": 0.15,        # 0.2 -> 0.15: continue concentrating in tightest flat regime
    "vol_floor_q": 0.5,             # only trade when ATR% above 30d median — MR edge is in high-vol-flat buckets per regime decomp
    "atr_period": 14,
}

PARAM_SPACE: dict = {
    "z_window": (24, 384),       # 6h .. 4d
    "entry_k": (1.0, 4.0),
    "exit_k": (0.0, 1.5),
    "long_only": (0, 1),
    "trend_ema": (20, 200),
    "trend_slope_window": (2, 20),
    "regime_lookback_days": (7, 90),
    "regime_quantile": (0.2, 0.8),
    "vol_floor_q": (0.0, 0.5),
    "atr_period": (7, 50),
}


def _positions_for_symbol(df: pd.DataFrame, params: dict, n_universe: int) -> pd.Series:
    """Per-symbol position with state-machine entry/exit on z-score."""
    close = df["close"]
    high, low = df["high"], df["low"]
    z_window = int(params.get("z_window", 96))
    entry_k = float(params.get("entry_k", 2.0))
    exit_k = float(params.get("exit_k", 0.5))
    long_only = int(params.get("long_only", 0)) == 1
    vol_floor_q = float(params.get("vol_floor_q", 0.2))
    atr_period = int(params.get("atr_period", 14))

    mean = close.rolling(z_window, min_periods=z_window).mean()
    std = close.rolling(z_window, min_periods=z_window).std()
    z = (close - mean) / std

    # --- 4h trend-regime gate (only fade when 4h trend is flat) ---
    trend_ema = int(params.get("trend_ema", 50))
    slope_window = int(params.get("trend_slope_window", 5))
    regime_lb_days = int(params.get("regime_lookback_days", 30))
    regime_q = float(params.get("regime_quantile", 0.5))

    df4h = resample_higher(
        df, "4h", {"close": "last"}, target_index=df.index
    )
    ema4h = df4h["close"].ewm(span=trend_ema, adjust=False, min_periods=trend_ema).mean()
    slope_4h = (ema4h - ema4h.shift(slope_window)) / ema4h
    abs_slope = slope_4h.abs()
    # 30d * 96 bars/day = 2880 bars
    lb_bars = max(regime_lb_days * 96, 96)
    slope_thresh = abs_slope.rolling(lb_bars, min_periods=lb_bars).quantile(regime_q)
    flat_regime = abs_slope <= slope_thresh
    flat_regime = flat_regime.fillna(False)

    # Vol-floor: skip entries when ATR% is in bottom quantile of 30d history.
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
    # ----------------------------------------------------------------

    # State machine on raw bar values; final .shift(1) defers to next bar.
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
            # Entries gated by flat regime AND vol-floor; exits always allowed.
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
    # Cross-sectional equal-weight: each symbol gets 1/n of total equity.
    pos = pos / max(n_universe, 1)
    return pos.shift(1).fillna(0.0)


# Raw sizing: position values are fractions of TOTAL equity, not slot fractions.
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
