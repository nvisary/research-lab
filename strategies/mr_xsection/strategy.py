"""Cross-sectional mean-reversion: long recent losers, short recent winners.

At each bar, rank the 10-major basket by N-bar trailing return.
Long the bottom-quantile (worst recent performers), short the top
(best recent performers). Bet that short-term relative extremes
revert to the basket mean.

This is the relative-strength inverse of momentum: the cross-section
spreads out (winners vs losers) and a fraction of that spread mean-
reverts within the basket. Independent of broad market direction —
in a bull market both legs go up but losers catch up; in chop both
trade around the basket centroid.

Per CLAUDE.md anti-pattern §2: cross-sectional with crypto majors
has survivorship bias (universe = currently-listed perps). Account
for ~30% Sharpe haircut when generalising forward.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DESCRIPTION = (
    "Cross-sectional mean-reversion. Each 4h bar, ranks the 10-major "
    "basket by 14-day trailing return; longs the bottom 30% (recent "
    "losers, expected to bounce) and shorts the top 30% (recent winners, "
    "expected to fade). Signal is RELATIVE within the basket — fires "
    "on dispersion, not on absolute price extremes."
)

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
]
DEFAULT_TF = "4h"

DEFAULT_PARAMS = {
    "lookback": 84,        # 14 days at 4h bars
    "long_quantile": 0.3,  # long bottom 30% (recent worst performers)
    "short_quantile": 0.3, # short top 30% (recent best performers)
    "long_only": 0,
}

PARAM_SPACE = {
    "lookback": (12, 360),
    "long_quantile": (0.1, 0.5),
    "short_quantile": (0.1, 0.5),
    "long_only": (0, 1),
}


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    lookback = int(params.get("lookback", 84))
    long_q = float(params.get("long_quantile", 0.3))
    short_q = float(params.get("short_quantile", 0.3))
    long_only = bool(int(params.get("long_only", 0)))
    if lookback < 2 or long_q <= 0 or long_q >= 1:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    # Wide close matrix, aligned across symbols.
    closes = pd.concat({s: df["close"] for s, df in data.items()}, axis=1)
    closes = closes.sort_index()
    if closes.shape[0] < lookback + 2 or closes.shape[1] < 2:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    # N-bar trailing return per symbol per bar.
    rets = closes.pct_change(lookback)

    # Cross-sectional percentile rank per row (NaN-safe).
    pct_rank = rets.rank(axis=1, pct=True, method="average")

    # Long the bottom-q (low rank → recent losers, expected to revert up).
    # Short the top-q (high rank → recent winners, expected to revert down).
    pos = pd.DataFrame(0.0, index=pct_rank.index, columns=pct_rank.columns)
    pos = pos.where(~(pct_rank <= long_q), 1.0)
    if not long_only:
        pos = pos.where(~(pct_rank >= 1 - short_q), -1.0)

    # No position where return is undefined (warmup or stale data).
    pos = pos.where(rets.notna(), 0.0)

    # No-lookahead: position at bar t derives from rank at bar t-1.
    pos = pos.shift(1).fillna(0.0)

    pos.index.name = "timestamp"
    pos.columns.name = "symbol"
    out = pos.stack().rename("position").reset_index()
    return out[["timestamp", "symbol", "position"]]
