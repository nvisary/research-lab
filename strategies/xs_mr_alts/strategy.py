"""xs_mr_alts — cross-sectional mean reversion on mid-cap alt perp basket.

Sibling to zscore_mr_alts. Same universe, different signal architecture.

At each 15m bar:
1. Compute lookback return per symbol (default 6h = 24 bars).
2. Demean across symbols to get residual (de-betas the basket move).
3. Rank residuals at the timestamp.
4. Long bottom `quantile` of names (worst recent residual — expecting reversion).
5. Short top `quantile` of names (best recent residual — expecting reversion).
6. Equal weight within each side; sum of |position| = 1.0 (100% gross,
   market-neutral by construction).

Thesis: the prior strategy (zscore_mr_alts) hit PF≈1.0 because per-symbol
z-score competes with the basket's net direction. Cross-sectional ranking is
naturally hedged: in a bear day, even the "best" alts may be down but they
still earn the relative-strength premium. The bull/bear regime bleed that
crushed per-symbol MR should largely disappear.

Caveat: survivorship bias is sharper here than in single-symbol MR. The
basket is "currently-listed" Bybit perps. Mid-caps that delisted between
2024 and 2026 (and would have been in a real-time XS basket) are missing.
For the iter loop this is a "discount the result" concern; real trading
would need delisting-aware data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from harness.utils import resample_higher


# Same universe as zscore_mr_alts: 24 mid-cap perps with full 2024-01 → 2026-04
# coverage, excluding top-5 by mcap (BTC/ETH/SOL/BNB/XRP). LTCUSDT dropped
# (only 16 of 28 months on disk).
DEFAULT_SYMBOLS: list[str] = [
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "TRXUSDT",
    "BCHUSDT", "NEARUSDT", "ATOMUSDT", "XLMUSDT", "OPUSDT",
    "INJUSDT", "SUIUSDT", "TIAUSDT", "SEIUSDT", "UNIUSDT",
    "FILUSDT", "HBARUSDT", "ICPUSDT", "LDOUSDT", "CRVUSDT",
    "SANDUSDT", "AXSUSDT", "IMXUSDT", "ETCUSDT",
]

DEFAULT_TF: str = "15min"

DEFAULT_PARAMS: dict = {
    "lookback": 96,       # 96 * 15m = 24h residual return window
    "quantile": 0.2,      # long bottom 20%, short top 20%
    "rebal_bars": 24,     # rebalance every N bars; hold cohort in between
    "long_only": 0,
    "regime_gate": 1,             # 1 = only trade when basket is flat
    "trend_ema": 50,              # 4h EMA span for basket trend
    "trend_slope_window": 5,      # bars to measure slope (5 * 4h = 20h)
    "regime_lookback_days": 30,
    "regime_quantile": 0.3,
    "vol_normalize": 1,           # 1 = rank residual / ATR% (vol-adjusted); 0 = raw residual
    "atr_period": 14,
}

PARAM_SPACE: dict = {
    "lookback": (4, 192),      # 1h .. 48h
    "quantile": (0.05, 0.5),
    "rebal_bars": (1, 192),
    "long_only": (0, 1),
    "regime_gate": (0, 1),
    "trend_ema": (20, 200),
    "trend_slope_window": (2, 20),
    "regime_lookback_days": (7, 90),
    "regime_quantile": (0.2, 0.8),
    "vol_normalize": (0, 1),
    "atr_period": (7, 50),
}


# Raw sizing: position values are fractions of TOTAL equity.
RAW_SIZING = True


def generate_signals(data: dict, params: dict) -> pd.DataFrame:
    lookback = int(params.get("lookback", 24))
    quantile = float(params.get("quantile", 0.2))
    rebal_bars = max(int(params.get("rebal_bars", 24)), 1)
    long_only = int(params.get("long_only", 0)) == 1
    regime_gate = int(params.get("regime_gate", 0)) == 1
    trend_ema = int(params.get("trend_ema", 50))
    slope_window = int(params.get("trend_slope_window", 5))
    regime_lb_days = int(params.get("regime_lookback_days", 30))
    regime_q = float(params.get("regime_quantile", 0.3))
    vol_normalize = int(params.get("vol_normalize", 0)) == 1
    atr_period = int(params.get("atr_period", 14))

    # Collect close prices in a wide frame keyed by timestamp, one column per symbol.
    closes = {}
    for sym, df in data.items():
        if df is None or df.empty:
            continue
        closes[sym] = df["close"]
    if not closes:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    close_wide = pd.DataFrame(closes).sort_index()
    n_sym = close_wide.shape[1]

    # Lookback return per symbol over the prior `lookback` bars.
    ret_n = close_wide.pct_change(lookback)

    # De-mean across symbols at each timestamp → residual return ("alpha vs basket").
    basket_mean = ret_n.mean(axis=1)
    residual = ret_n.sub(basket_mean, axis=0)

    # Optionally normalize each symbol's residual by its own ATR% — gives
    # "moves in own-vol units" instead of "moves in absolute return". Reduces
    # ranking bias toward high-vol symbols (which dominate raw residual rankings
    # by construction).
    if vol_normalize:
        atr_pct_wide = {}
        for sym, df in data.items():
            if df is None or df.empty:
                continue
            prev_close = df["close"].shift(1)
            tr = pd.concat([
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ], axis=1).max(axis=1)
            atr = tr.ewm(span=atr_period, adjust=False, min_periods=atr_period).mean()
            atr_pct_wide[sym] = atr / df["close"]
        atr_pct_frame = pd.DataFrame(atr_pct_wide).reindex(close_wide.index).reindex(columns=close_wide.columns)
        # Avoid div-by-zero. Floor at a small epsilon.
        residual = residual / atr_pct_frame.replace(0.0, np.nan)

    # Per-bar rank across symbols (NaN rows get NaN ranks, will be ignored below).
    ranks = residual.rank(axis=1, method="first", ascending=True)
    n_per_side = max(1, int(round(n_sym * quantile)))

    # Long bottom n_per_side, short top n_per_side.
    long_mask = ranks <= n_per_side
    short_mask = ranks > (n_sym - n_per_side)

    pos_wide = pd.DataFrame(0.0, index=close_wide.index, columns=close_wide.columns)
    weight = 1.0 / (2 * n_per_side)  # equal split across the 2k active positions
    pos_wide = pos_wide.mask(long_mask, weight)
    if not long_only:
        pos_wide = pos_wide.mask(short_mask, -weight)
    else:
        # Long-only variant: double the long weight so we use the same gross
        # exposure budget (gross = 1.0) when shorts are disabled.
        pos_wide = pos_wide.mask(long_mask, 2 * weight)

    # Basket-level 4h trend regime gate: only trade when the equal-weighted
    # basket itself is in a flat regime. Iter 5 showed XS-MR has same
    # bull-bucket leakage as per-symbol MR — basket trend matters.
    if regime_gate:
        basket = pd.DataFrame({"close": close_wide.mean(axis=1)})
        basket_4h = resample_higher(basket, "4h", {"close": "last"}, target_index=basket.index)
        ema_4h = basket_4h["close"].ewm(span=trend_ema, adjust=False, min_periods=trend_ema).mean()
        slope_4h = (ema_4h - ema_4h.shift(slope_window)) / ema_4h
        abs_slope = slope_4h.abs()
        lb_bars = max(regime_lb_days * 96, 96)
        slope_thresh = abs_slope.rolling(lb_bars, min_periods=lb_bars).quantile(regime_q)
        flat_basket = (abs_slope <= slope_thresh).fillna(False)
        # Mask positions: when basket trending, sit flat across the whole basket.
        pos_wide = pos_wide.where(flat_basket, 0.0)

    # No-lookahead shift: position at bar t is decided from data ≤ t-1.
    pos_wide = pos_wide.shift(1).fillna(0.0)

    # Rebalance only every `rebal_bars` bars; hold positions in between.
    # This caps churn: with rebal_bars=24 and ~10 active positions per bar,
    # max turnover is 24x lower than the rebal-every-bar baseline.
    if rebal_bars > 1:
        bar_idx = np.arange(len(pos_wide))
        keep = pd.Series((bar_idx % rebal_bars) == 0, index=pos_wide.index)
        pos_wide = pos_wide.where(keep, np.nan).ffill().fillna(0.0)

    # Long-format output. Reindex each symbol's positions back to its own
    # native df.index in case wide-frame alignment differs (it shouldn't,
    # but be defensive).
    frames = []
    for sym, df in data.items():
        if df is None or df.empty or sym not in pos_wide.columns:
            continue
        pos = pos_wide[sym].reindex(df.index).fillna(0.0)
        frames.append(pd.DataFrame({
            "timestamp": df.index,
            "symbol": sym,
            "position": pos.values,
        }))
    if not frames:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(frames, ignore_index=True)
