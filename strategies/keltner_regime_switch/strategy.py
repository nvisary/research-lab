"""Keltner regime-switch — momentum in trends, mean-reversion in chop.

The hypothesis: mean-reversion and momentum are not competing strategies
but complementary regimes of the same market microstructure. In
trending regimes (high ADX), break-of-band continues — pay the move.
In ranging regimes (low ADX), break-of-band reverts — fade it. Both
sub-strategies use the same Keltner channel as the trade signal; only
the SIGN of the response flips by regime.

Architecture:
    1. Compute Keltner: EMA(20) ± 2.0·ATR(10).
    2. Compute regime classifier: ADX(14).
    3. Branch:
         ADX > trend_threshold (default 25):  momentum / breakout
             pos = +1 if close > upper, -1 if close < lower, else 0
         ADX < range_threshold (default 20):  mean-reversion / fade
             pos = clip( -(close - middle) / (mult·atr),  -1, +1 )
             Continuous fade: full short at upper band, full long at
             lower, scaled linearly through middle. No state machine,
             no entry/exit semantics — vectorbt rebalances each bar.
         else (dead zone):                    flat
             pos = 0   (hysteresis to prevent flickering on regime
                       boundary)
    4. shift(1) for no-lookahead.

Why continuous fade and not discrete entries:
    - Pure "long at lower, exit at middle" requires state-tracking
      (was-in-trade?) which is hard to vectorize and easy to leak
      lookahead. The continuous-fade formulation has the same
      directional bias (long below middle, short above) without
      needing memory between bars.
    - In the ranging regime the asset spends most of its time near
      the middle, so position size is naturally small most of the
      time and grows as price approaches an extreme. Cost-aware.

Why ADX as regime classifier:
    - Well-understood, standard. Bounded [0, 100] so thresholds are
      interpretable.
    - Direction-agnostic: tells you "is there a directional move"
      without saying which way.

Default thresholds (25 / 20) come from Wilder's original work and are
the most-cited values. The 5-point dead zone provides hysteresis so
the strategy doesn't ping-pong between regimes when ADX hovers near
the boundary.

Single-symbol BTCUSDT baseline by design — the regime-switch
mechanism is the variable under test. Multi-symbol diversification
(which we know works) is an independent dimension; add it only after
the switch is validated on a single asset.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
]
DEFAULT_TF = "4h"

DEFAULT_PARAMS = {
    "ema_period": 20,
    "atr_period": 10,
    "multiplier": 2.0,
    "adx_period": 14,
    "trend_threshold": 25.0,   # ADX > this → momentum/breakout regime
    "range_threshold": 20.0,   # ADX < this → mean-reversion regime
    "long_only": 0,            # 0 = long+short, 1 = long-only
    # Sub-strategy enable flags so ablation tests are one-flag changes
    # (METHODS §6.1 anti-pattern: don't tune; just toggle to attribute
    # contributions cleanly).
    "enable_momentum": 1,
    "enable_meanrev": 0,  # ablate MR with 10-symbol basket (enough trades now)
    # 1d EMA trend gate applied to momentum side only.
    # In pure breakout this was the largest win (composite -1.10 -> -0.52).
    # MR side fights higher-TF trend by design, so leave it ungated.
    "htf_ema_period": 50,
    # Adaptive ADX-quantile classifier. When > 0, replaces fixed
    # trend/range thresholds with rolling-window terciles of ADX itself.
    # Hypothesis: ADX distribution shifts across regimes; fixed 25/20
    # mis-classify when typical ADX is uniformly higher or lower.
    "adx_quantile_window": 90,   # ~15 days at 4h
}

PARAM_SPACE = {
    "ema_period": (5, 100),
    "atr_period": (5, 50),
    "multiplier": (1.0, 4.0),
    "adx_period": (5, 30),
    "trend_threshold": (15.0, 40.0),
    "range_threshold": (10.0, 30.0),
    "long_only": (0, 1),
    "enable_momentum": (0, 1),
    "enable_meanrev": (0, 1),
    "htf_ema_period": (10, 200),
    "adx_quantile_window": (0, 250),
}


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
    """Average Directional Index, Wilder smoothing."""
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
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    return adx


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    ema_period = int(params.get("ema_period", 20))
    atr_period = int(params.get("atr_period", 10))
    multiplier = float(params.get("multiplier", 2.0))
    adx_period = int(params.get("adx_period", 14))
    trend_threshold = float(params.get("trend_threshold", 25.0))
    range_threshold = float(params.get("range_threshold", 20.0))
    long_only = bool(int(params.get("long_only", 0)))
    enable_momentum = bool(int(params.get("enable_momentum", 1)))
    enable_meanrev = bool(int(params.get("enable_meanrev", 1)))
    htf_ema_period = int(params.get("htf_ema_period", 0))
    adx_quantile_window = int(params.get("adx_quantile_window", 0))

    if (ema_period < 2 or atr_period < 2 or adx_period < 2
            or multiplier <= 0
            or range_threshold >= trend_threshold):
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    rows: list[pd.DataFrame] = []
    for sym, df in data.items():
        if df.empty or len(df) < max(ema_period, atr_period, adx_period) + 5:
            continue
        close = df["close"]
        middle = close.ewm(span=ema_period, adjust=False,
                           min_periods=ema_period).mean()
        atr = _wilder_atr(df["high"], df["low"], close, atr_period)
        upper = middle + multiplier * atr
        lower = middle - multiplier * atr
        adx = _wilder_adx(df["high"], df["low"], close, adx_period)

        # Momentum / breakout sub-signal: discrete +1/-1/0
        momentum = pd.Series(0.0, index=df.index)
        momentum = momentum.where(~(close > upper), 1.0)
        momentum = momentum.where(~(close < lower), -1.0)

        # 1d EMA trend gate on momentum side (proven win in pure breakout).
        # Use resample_higher (lookahead-safe) to align previous COMPLETED
        # 1d bar's EMA to current 4h index, then permit longs only above
        # the 1d EMA and shorts only below.
        if htf_ema_period > 0:
            df_1d = resample_higher(
                df, "1D", {"close": "last"}, target_index=df.index,
            )
            htf_ema = df_1d["close"].ewm(
                span=htf_ema_period, adjust=False,
                min_periods=htf_ema_period,
            ).mean()
            htf_bull = (df_1d["close"] > htf_ema).fillna(False)
            htf_bear = (df_1d["close"] < htf_ema).fillna(False)
            mom_long = (momentum > 0) & htf_bull
            mom_short = (momentum < 0) & htf_bear
            momentum = pd.Series(0.0, index=df.index).where(
                ~mom_long, 1.0
            ).where(~mom_short, -1.0)

        # Mean-reversion sub-signal: continuous fade.
        # full short (-1) at upper, full long (+1) at lower, linear through middle.
        band_half = (multiplier * atr).replace(0, np.nan)
        meanrev = (-(close - middle) / band_half).clip(-1.0, 1.0).fillna(0.0)

        # Regime selector. If adx_quantile_window > 0, use rolling-tercile
        # quantiles of ADX itself (top tercile = trend, bottom = range).
        # Adaptive to whatever ADX distribution the current regime exhibits.
        if adx_quantile_window > 0:
            adx_hi = adx.rolling(adx_quantile_window,
                                 min_periods=adx_quantile_window).quantile(0.66)
            adx_lo = adx.rolling(adx_quantile_window,
                                 min_periods=adx_quantile_window).quantile(0.33)
            is_trend = adx > adx_hi
            is_range = adx < adx_lo
        else:
            is_trend = adx > trend_threshold
            is_range = adx < range_threshold

        direction = pd.Series(0.0, index=df.index)
        if enable_momentum:
            direction = direction.where(~is_trend, momentum)
        if enable_meanrev:
            direction = direction.where(~is_range, meanrev)
        # Dead zone (range_threshold <= ADX <= trend_threshold) stays 0.

        if long_only:
            direction = direction.clip(lower=0.0)

        # No-lookahead shift.
        pos = direction.shift(1).fillna(0.0)

        rows.append(pd.DataFrame({
            "timestamp": df.index, "symbol": sym, "position": pos.values,
        }))

    if not rows:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(rows, ignore_index=True)
