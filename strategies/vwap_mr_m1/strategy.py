"""vwap_mr_m1 — VWAP Mean Reversion on 1-minute bars.

Refinement:
- Reduced universe to 15 symbols (to avoid timeout).
- Loosened filters slightly (25% quantile).
- Sigma_k 3.8.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher

DEFAULT_SYMBOLS: list[str] = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "OPUSDT",
    "ARBUSDT",
    "NEARUSDT",
]

DEFAULT_TF: str = "1min"

DEFAULT_PARAMS: dict = {
    "vwap_window": 120,  # 2 hours
    "sigma_k": 3.8,  # 3.8 standard deviations
    "sl_pct": 0.012,  # 1.2% stop loss
    "long_only": 0,
    # Trend filter (EMA based)
    "trend_ema": 50,
    "slope_window": 12,  # 12 * 1h = 12h
    "regime_quantile": 0.25,  # only trade in bottom 25% of slope volatility
    "regime_lb_days": 14,
    # VWAP slope filter
    "vwap_slope_window": 30,  # 30 mins
    "vwap_slope_q": 0.25,  # only trade if VWAP is in bottom 25% of its movement
    # Vol floor
    "vol_floor_q": 0.2,  # skip bottom 20% of vol
    "atr_period": 14,
}

PARAM_SPACE: dict = {
    "vwap_window": (60, 240),
    "sigma_k": (3.0, 5.0),
    "sl_pct": (0.005, 0.02),
}


def _positions_for_symbol(df: pd.DataFrame, params: dict, n_universe: int) -> pd.Series:
    close_ser = df["close"]
    close = close_ser.values

    vwap_window = int(params.get("vwap_window", 120))
    sigma_k = float(params.get("sigma_k", 3.8))
    sl_pct = float(params.get("sl_pct", 0.012))
    long_only = int(params.get("long_only", 0)) == 1

    # Typical Price for VWAP
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = tp * df["volume"]

    # Sliding VWAP
    rolling_pv = pv.rolling(vwap_window, min_periods=vwap_window).sum()
    rolling_vol = df["volume"].rolling(vwap_window, min_periods=vwap_window).sum()
    vwap_ser = rolling_pv / rolling_vol
    vwap = vwap_ser.values

    # Standard Deviation
    std = df["close"].rolling(vwap_window, min_periods=vwap_window).std().values

    upper_band = vwap + sigma_k * std
    lower_band = vwap - sigma_k * std

    # --- 1. Trend Filter (EMA Slope) ---
    trend_ema = int(params.get("trend_ema", 50))
    slope_window = int(params.get("slope_window", 12))
    regime_lb_days = int(params.get("regime_lb_days", 14))
    regime_q = float(params.get("regime_quantile", 0.25))

    df1h = resample_higher(df, "1h", {"close": "last"}, target_index=df.index)
    ema1h = (
        df1h["close"].ewm(span=trend_ema, adjust=False, min_periods=trend_ema).mean()
    )
    slope_1h = (ema1h - ema1h.shift(slope_window)) / ema1h
    abs_slope = slope_1h.abs()

    lb_bars = max(regime_lb_days * 1440, 1440)
    slope_thresh = abs_slope.rolling(
        lb_bars, min_periods=min(lb_bars, len(abs_slope))
    ).quantile(regime_q)
    flat_regime = (abs_slope <= slope_thresh).fillna(False).values

    # --- 2. VWAP Slope Filter ---
    vwap_slope_window = int(params.get("vwap_slope_window", 30))
    vwap_slope_q = float(params.get("vwap_slope_q", 0.25))

    vwap_slope = (vwap_ser - vwap_ser.shift(vwap_slope_window)) / vwap_ser
    abs_vwap_slope = vwap_slope.abs()
    vwap_slope_thresh = abs_vwap_slope.rolling(
        lb_bars, min_periods=min(lb_bars, len(abs_vwap_slope))
    ).quantile(vwap_slope_q)
    vwap_flat = (abs_vwap_slope <= vwap_slope_thresh).fillna(False).values

    # --- 3. Vol Floor ---
    vol_floor_q = float(params.get("vol_floor_q", 0.2))
    atr_period = int(params.get("atr_period", 14))

    prev_close = close_ser.shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(span=atr_period, adjust=False, min_periods=atr_period).mean()
    atr_pct = atr / close_ser
    vol_thresh = atr_pct.rolling(
        lb_bars, min_periods=min(lb_bars, len(atr_pct))
    ).quantile(vol_floor_q)
    vol_ok = (atr_pct >= vol_thresh).fillna(False).values

    # --- State Machine ---
    state = np.zeros(len(close), dtype=np.float64)
    cur = 0.0
    entry_price = 0.0

    for i in range(1, len(close)):
        if np.isnan(vwap[i]) or np.isnan(std[i]):
            continue

        if cur == 0.0:
            # Entry gates
            if flat_regime[i] and vwap_flat[i] and vol_ok[i]:
                # Long Entry
                if close[i - 1] < lower_band[i - 1] and close[i] > lower_band[i]:
                    cur = 1.0
                    entry_price = close[i]
                # Short Entry
                elif (
                    not long_only
                    and close[i - 1] > upper_band[i - 1]
                    and close[i] < upper_band[i]
                ):
                    cur = -1.0
                    entry_price = close[i]
        elif cur == 1.0:
            if close[i] >= vwap[i]:
                cur = 0.0
            elif close[i] <= entry_price * (1 - sl_pct):
                cur = 0.0
        elif cur == -1.0:
            if close[i] <= vwap[i]:
                cur = 0.0
            elif close[i] >= entry_price * (1 + sl_pct):
                cur = 0.0

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
        frames.append(
            pd.DataFrame(
                {
                    "timestamp": df.index,
                    "symbol": symbol,
                    "position": pos.values,
                }
            )
        )
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
