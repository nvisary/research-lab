"""zret_mr_alts — per-symbol RETURN-based z mean reversion on mid-cap alt perps.

Branched from `zscore_mr_alts` (best iter 6 state). Single structural change:
the z-score is computed on the N-bar log return, not on the raw price level.

Per symbol on 15m bars:
- r_N = log(close) - log(close[-z_window])   — N-bar log return ending at t
- z = (r_N - SMA(r_N, z_window)) / std(r_N, z_window)  — z over a rolling
  baseline of own N-bar returns
- enter LONG  when z < -entry_k  (recent run is unusually large to the downside)
- enter SHORT when z >  entry_k  (recent run is unusually large to the upside)
- exit when |z| < exit_k
- 4h trend-regime gate + vol-floor — kept from zscore_mr_alts.

Why this might beat price-z:
Price-z catches both blow-off events AND slow trend grinds (in a smooth
uptrend, close is at top of recent range so z is persistently elevated, but
the move doesn't mean-revert — slow trend ≠ overextension). Return-z stays
near zero in steady trends (each N-bar return looks like the typical N-bar
return) and only spikes when the *recent return is unusually extreme vs its
own recent distribution* — closer to the "shock that should revert" pattern.

The same regime+vol filters apply as in zscore_mr_alts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher


# Same universe as zscore_mr_alts: 24 mid-cap perps with full 2024-01 → 2026-04
# monthly coverage on disk.
DEFAULT_SYMBOLS: list[str] = [
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "TRXUSDT",
    "BCHUSDT", "NEARUSDT", "ATOMUSDT", "XLMUSDT", "OPUSDT",
    "INJUSDT", "SUIUSDT", "TIAUSDT", "SEIUSDT", "UNIUSDT",
    "FILUSDT", "HBARUSDT", "ICPUSDT", "LDOUSDT", "CRVUSDT",
    "SANDUSDT", "AXSUSDT", "IMXUSDT", "ETCUSDT",
]

DEFAULT_TF: str = "15min"

DEFAULT_PARAMS: dict = {
    "z_window": 96,                 # used both for r_N lookback AND for the
                                    # baseline z. 96 * 15m = 24h.
    "entry_k": 2.0,
    "exit_k": 1.0,
    "long_only": 0,
    # 4h trend-regime gate (carried over from zscore_mr_alts best).
    "trend_ema": 50,
    "trend_slope_window": 5,
    "regime_lookback_days": 30,
    "regime_quantile": 0.2,
    "vol_floor_q": 0.65,
    "atr_period": 7,
}

PARAM_SPACE: dict = {
    "z_window": (24, 384),
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
    """Per-symbol position with state-machine entry/exit on return-z."""
    close = df["close"]
    high, low = df["high"], df["low"]
    z_window = int(params.get("z_window", 96))
    entry_k = float(params.get("entry_k", 2.0))
    exit_k = float(params.get("exit_k", 0.5))
    long_only = int(params.get("long_only", 0)) == 1
    vol_floor_q = float(params.get("vol_floor_q", 0.2))
    atr_period = int(params.get("atr_period", 14))

    # *** The structural change vs zscore_mr_alts ***
    # N-bar log return: log(close[t]) - log(close[t-z_window]).
    log_close = np.log(close)
    ret_n = log_close - log_close.shift(z_window)
    # Z-score this against its own rolling distribution over z_window bars.
    # Effective signal lookback is 2*z_window (need z_window bars to compute
    # ret_n, then another z_window bars to compute its rolling stats).
    mean_r = ret_n.rolling(z_window, min_periods=z_window).mean()
    std_r = ret_n.rolling(z_window, min_periods=z_window).std()
    z = (ret_n - mean_r) / std_r

    # --- 4h trend-regime gate (only fade when 4h trend is flat) ---
    trend_ema = int(params.get("trend_ema", 50))
    slope_window = int(params.get("trend_slope_window", 5))
    regime_lb_days = int(params.get("regime_lookback_days", 30))
    regime_q = float(params.get("regime_quantile", 0.3))

    df4h = resample_higher(df, "4h", {"close": "last"}, target_index=df.index)
    ema4h = df4h["close"].ewm(span=trend_ema, adjust=False, min_periods=trend_ema).mean()
    slope_4h = (ema4h - ema4h.shift(slope_window)) / ema4h
    abs_slope = slope_4h.abs()
    lb_bars = max(regime_lb_days * 96, 96)
    slope_thresh = abs_slope.rolling(lb_bars, min_periods=lb_bars).quantile(regime_q)
    flat_regime = (abs_slope <= slope_thresh).fillna(False)

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
