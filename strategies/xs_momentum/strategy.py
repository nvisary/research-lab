"""Cross-sectional momentum — market-neutral basket strategy.

The hypothesis: in any given window, recently-strong assets continue
to outperform recently-weak ones in the cross-section, even when the
absolute direction of the market is mixed. By going long the winners
and short the losers in equal $-weight, we hedge out market beta and
capture the relative-strength dispersion.

References:
    - Jegadeesh & Titman (1993). Returns to buying winners and selling
      losers: implications for stock market efficiency.
    - Asness, Moskowitz, Pedersen (2013). Value and momentum everywhere.
      Crypto-applied evidence is thinner but the same structural mechanism.

Architecture:
    For each bar t:
        score[t, sym] = log(close[t-1] / close[t-1-lookback_bars])   per symbol
        rank[t, sym]  = score's percentile across symbols at t
        position[t, sym] = +1/N_long  if rank in top quantile
                          = -1/N_short if rank in bottom quantile
                          = 0          otherwise
    Sum of positions per bar ≈ 0 (market-neutral by construction).

The "winning" symbols vary by bar; the basket implicitly rotates.
With 10 symbols and 20% top/bot, that's 2 longs and 2 shorts active
at any time — fairly concentrated, but each symbol is well-known
liquid and capacity is ample.

Lookback choice:
    180 bars at 4h ≈ 30 calendar days. Academic literature on weekly
    horizons clusters around 1-12 month lookbacks; 30 days is the
    median compromise. Shorter (1-2 weeks) tends to capture noise;
    longer (90+ days) misses regime turns. The agent should
    re-evaluate this — 1d TF + longer lookback may dominate.

Costs note:
    Cross-sectional rebalances on every bar. Per-bar position changes
    can be small (rank rarely flips entirely), but the strategy still
    pays continuous slippage. The harness models this; the operator
    will see it materialise as `turnover` and the size-impact slippage
    component (when --cost-model spread or full).

Multi-symbol freedom:
    Universe is the 10-major basket from prior research. The agent
    can extend to broader universes (50+ symbols) but must keep
    survivorship bias in mind (documented in README).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
]
DEFAULT_TF = "4h"

DEFAULT_PARAMS = {
    "lookback_bars": 360,    # ~60d at 4h (academic momentum standard)
    "top_quantile": 0.20,    # top 20% long
    "bot_quantile": 0.20,    # bottom 20% short
    "long_only": 1,          # 0 = market-neutral long+short, 1 = long-only
    "min_symbols": 5,        # require at least N symbols with valid scores
}

PARAM_SPACE = {
    "lookback_bars": (24, 720),         # 4h .. 4mo
    "top_quantile": (0.10, 0.50),
    "bot_quantile": (0.10, 0.50),
    "long_only": (0, 1),
    "min_symbols": (3, 10),
}


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    lookback_bars = int(params.get("lookback_bars", 180))
    top_quantile = float(params.get("top_quantile", 0.20))
    bot_quantile = float(params.get("bot_quantile", 0.20))
    long_only = bool(int(params.get("long_only", 0)))
    min_symbols = int(params.get("min_symbols", 5))

    if (lookback_bars < 5 or top_quantile <= 0 or top_quantile >= 1
            or bot_quantile <= 0 or bot_quantile >= 1):
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    # Wide DataFrame of close prices, columns = symbols, aligned on a
    # union index so ranking compares contemporaneous values.
    closes = pd.DataFrame({
        sym: df["close"]
        for sym, df in data.items()
        if not df.empty
    })
    if closes.empty or closes.shape[1] < min_symbols:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    # Score: log-return over the lookback window.
    # Use shift(1) on the input so score at bar t depends only on bars <= t-1.
    closes_lag = closes.shift(1)
    score = np.log(closes_lag / closes_lag.shift(lookback_bars))

    # Mask out rows with too few valid scores (early period before lookback fills).
    valid_per_row = score.notna().sum(axis=1)
    score_masked = score.where(valid_per_row >= min_symbols, np.nan)

    # Per-row percentile rank in [0, 1]. NaN scores get NaN ranks
    # (they don't take any position).
    ranks = score_masked.rank(axis=1, pct=True, method="average")

    # Top quantile = highest rank (winners → long).
    # Bottom quantile = lowest rank (losers → short).
    top_threshold = 1.0 - top_quantile
    bot_threshold = bot_quantile
    long_mask = ranks > top_threshold
    short_mask = ranks <= bot_threshold

    # Equal-weight within each leg. N_long varies bar-by-bar (depends on
    # how many symbols have valid scores AND fall above threshold).
    n_long = long_mask.sum(axis=1).replace(0, np.nan)
    n_short = short_mask.sum(axis=1).replace(0, np.nan)

    pos = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    long_weights = (1.0 / n_long).reindex(pos.index)
    short_weights = (-1.0 / n_short).reindex(pos.index)
    # Broadcast row-wise: same weight for every long, same for every short.
    pos = pos.where(~long_mask, long_weights, axis=0)
    pos = pos.where(~short_mask, short_weights, axis=0)
    pos = pos.fillna(0.0)

    if long_only:
        pos = pos.clip(lower=0.0)

    # No double-shift: score already used closes_lag = shift(1). Position
    # at bar t is computed entirely from bars <= t-1.
    rows: list[pd.DataFrame] = []
    for sym in pos.columns:
        rows.append(pd.DataFrame({
            "timestamp": pos.index,
            "symbol": sym,
            "position": pos[sym].values,
        }))
    if not rows:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])
    return pd.concat(rows, ignore_index=True)
