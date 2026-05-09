"""Bollinger Bands + RSI mean-reversion in ranging markets — baseline.

Hypothesis: in low-trend (ranging) regimes, price oscillates around a
volatility-adaptive mean. Bollinger Bands quantify that envelope; RSI
confirms exhaustion at the extremes; ADX gates out the trend regime
where mean-reversion gets ground up. Exit on a tag of the BB middle
(profit) or an ATR-multiple stop beyond the entry extreme.

Rules (per symbol, per bar):
  Indicators
    BB(period=20, std=2.0)        — upper / mid / lower
    RSI(14)
    ADX(14)
    ATR(14)                       — for the stop distance

  Entry (only when flat, only when ADX[t] < adx_max — "ranging"):
    long  if  close[t] <= lower[t]  AND  rsi[t] < rsi_long
    short if  close[t] >= upper[t]  AND  rsi[t] > rsi_short

  Exit (while in trade):
    long  : close[t] >= mid[t]   (take, mid-band tag)
            OR low[t]  <= entry_price - atr_stop_mult * atr_at_entry
    short : close[t] <= mid[t]
            OR high[t] >= entry_price + atr_stop_mult * atr_at_entry

  Position emitted for bar t is the post-entry/exit state at end of
  bar t-1 (.shift(1)). All decisions use only data up to and
  including the prior bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
]
DEFAULT_TF = "1h"

DEFAULT_PARAMS = {
    "bb_period": 20,
    "bb_std": 2.5,
    "rsi_period": 14,
    "rsi_long": 100.0,   # long entry: rsi < this  (100 = disabled)
    "rsi_short": 0.0,    # short entry: rsi > this (0 = disabled)
    "adx_period": 14,
    "adx_max": 25.0,     # only trade when ADX < adx_max (ranging)
    "atr_period": 14,
    "atr_stop_mult": 3.5,
    "long_only": 1,      # 0 = long+short, 1 = long-only
    "htf_ema_period": 100,  # 1d EMA gate: longs only when 1d close > 1d EMA (0 disables)
}

PARAM_SPACE = {
    "bb_period": (10, 60),
    "bb_std": (1.5, 3.0),
    "rsi_period": (5, 30),
    "rsi_long": (15.0, 40.0),
    "rsi_short": (60.0, 85.0),
    "adx_period": (5, 30),
    "adx_max": (10.0, 30.0),
    "atr_period": (5, 30),
    "atr_stop_mult": (0.5, 4.0),
    "long_only": (0, 1),
    "htf_ema_period": (0, 200),
}


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _wilder_adx(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False,
                                  min_periods=period).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False,
                                    min_periods=period).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()


def _state_machine(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                   upper: np.ndarray, mid: np.ndarray, lower: np.ndarray,
                   rsi: np.ndarray, adx: np.ndarray, atr: np.ndarray,
                   htf_long_ok: np.ndarray, htf_short_ok: np.ndarray,
                   rsi_long: float, rsi_short: float, adx_max: float,
                   atr_stop_mult: float, long_only: bool) -> np.ndarray:
    """Per-bar state machine. Returns direction[t] = state at end of bar t."""
    n = close.shape[0]
    out = np.zeros(n, dtype=np.float64)
    pos = 0      # -1, 0, +1
    entry_px = 0.0
    entry_atr = 0.0
    for t in range(n):
        c = close[t]; h = high[t]; lo = low[t]
        u = upper[t]; m = mid[t]; lb = lower[t]
        r = rsi[t]; a = adx[t]; at = atr[t]

        # any input NaN -> stay flat / preserve state but no decisions
        if (not np.isfinite(c)) or (not np.isfinite(u)) or (not np.isfinite(lb)) \
                or (not np.isfinite(r)) or (not np.isfinite(a)) or (not np.isfinite(at)):
            out[t] = pos
            continue

        # --- exit while in trade ---
        if pos > 0:
            stop_hit = lo <= entry_px - atr_stop_mult * entry_atr
            take_hit = c >= m
            if stop_hit or take_hit:
                pos = 0
        elif pos < 0:
            stop_hit = h >= entry_px + atr_stop_mult * entry_atr
            take_hit = c <= m
            if stop_hit or take_hit:
                pos = 0

        # --- entry only when flat and ranging (+ HTF trend gate) ---
        if pos == 0 and a < adx_max:
            if c <= lb and r < rsi_long and htf_long_ok[t]:
                pos = 1
                entry_px = c
                entry_atr = at
            elif (not long_only) and c >= u and r > rsi_short and htf_short_ok[t]:
                pos = -1
                entry_px = c
                entry_atr = at

        out[t] = pos
    return out


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    bb_period = int(params.get("bb_period", 20))
    bb_std = float(params.get("bb_std", 2.0))
    rsi_period = int(params.get("rsi_period", 14))
    rsi_long = float(params.get("rsi_long", 30.0))
    rsi_short = float(params.get("rsi_short", 70.0))
    adx_period = int(params.get("adx_period", 14))
    adx_max = float(params.get("adx_max", 20.0))
    atr_period = int(params.get("atr_period", 14))
    atr_stop_mult = float(params.get("atr_stop_mult", 1.5))
    long_only = bool(int(params.get("long_only", 0)))
    htf_ema_period = int(params.get("htf_ema_period", 0))
    if bb_period < 2 or rsi_period < 2 or adx_period < 2 or atr_period < 2:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    rows: list[pd.DataFrame] = []
    for sym, df in data.items():
        if df.empty or len(df) < max(bb_period, rsi_period, adx_period, atr_period) + 5:
            continue
        close = df["close"]
        high = df["high"]
        low = df["low"]

        mid = close.rolling(bb_period, min_periods=bb_period).mean()
        sd = close.rolling(bb_period, min_periods=bb_period).std(ddof=0)
        upper = mid + bb_std * sd
        lower = mid - bb_std * sd

        rsi = _rsi(close, rsi_period)
        adx = _wilder_adx(high, low, close, adx_period)
        atr = _wilder_atr(high, low, close, atr_period)

        # HTF (1d) trend gate. Resample_higher applies the safe one-bar
        # shift on the 1d series so the value at decision-time t comes
        # from the previous COMPLETED 1d bar (lookahead-safe).
        if htf_ema_period >= 2:
            df_1d = resample_higher(df, "1D", {"close": "last"},
                                    target_index=df.index)
            htf_close = df_1d["close"].ffill()
            htf_ema = htf_close.ewm(span=htf_ema_period, adjust=False,
                                    min_periods=htf_ema_period).mean()
            htf_long_ok = (htf_close > htf_ema).fillna(False).to_numpy()
            htf_short_ok = (htf_close < htf_ema).fillna(False).to_numpy()
        else:
            n = len(close)
            htf_long_ok = np.ones(n, dtype=bool)
            htf_short_ok = np.ones(n, dtype=bool)

        direction = _state_machine(
            close.to_numpy(dtype=np.float64),
            high.to_numpy(dtype=np.float64),
            low.to_numpy(dtype=np.float64),
            upper.to_numpy(dtype=np.float64),
            mid.to_numpy(dtype=np.float64),
            lower.to_numpy(dtype=np.float64),
            rsi.to_numpy(dtype=np.float64),
            adx.to_numpy(dtype=np.float64),
            atr.to_numpy(dtype=np.float64),
            htf_long_ok, htf_short_ok,
            rsi_long, rsi_short, adx_max, atr_stop_mult, long_only,
        )

        # No-lookahead: position at bar t is state at end of bar t-1.
        pos = pd.Series(direction, index=df.index).shift(1).fillna(0.0)

        rows.append(pd.DataFrame({
            "timestamp": df.index, "symbol": sym, "position": pos.values,
        }))

    if not rows:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(rows, ignore_index=True)
