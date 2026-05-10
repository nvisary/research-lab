"""Cross-sectional momentum: long winners, short losers.

At each 1d bar, rank the 10-major basket by 60-day trailing return.
Long the top quantile (best recent performers), short the bottom
(worst). Bet that recent relative strength persists over the next
horizon.

Classic Jegadeesh-Titman cross-sectional momentum, applied to crypto.
Independent of broad market direction — picks up dispersion within
the basket. In a uniform bull market the long leg captures the
strongest movers and the short leg fades the laggards.

Survivorship caveat (per AGENTS.md §10d): the universe is
currently-listed Bybit perps. Coins that delisted are not in the
basket, biasing CSM results upward. Account for ~30% Sharpe haircut
when generalising forward.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DESCRIPTION = (
    "Cross-sectional momentum. Each 1d bar, ranks the 10-major basket "
    "by 60-day trailing return; longs the top 30% (recent winners, "
    "expected to keep winning) and shorts the bottom 30%. Classic "
    "Jegadeesh-Titman momentum factor on crypto majors — fires on "
    "RELATIVE strength dispersion, market-direction-neutral."
)

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
]
DEFAULT_TF = "1d"

DEFAULT_PARAMS = {
    "lookback": 60,         # days of trailing return for ranking
    "long_quantile": 0.3,   # long top 30%
    "short_quantile": 0.3,  # short bottom 30%
    "long_only": 0,
}

PARAM_SPACE = {
    "lookback": (5, 180),
    "long_quantile": (0.1, 0.5),
    "short_quantile": (0.1, 0.5),
    "long_only": (0, 1),
}


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    lookback = int(params.get("lookback", 60))
    long_q = float(params.get("long_quantile", 0.3))
    short_q = float(params.get("short_quantile", 0.3))
    long_only = bool(int(params.get("long_only", 0)))
    if lookback < 2 or long_q <= 0 or long_q >= 1:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    closes = pd.concat({s: df["close"] for s, df in data.items()}, axis=1)
    closes = closes.sort_index()
    if closes.shape[0] < lookback + 2 or closes.shape[1] < 2:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    rets = closes.pct_change(lookback)
    pct_rank = rets.rank(axis=1, pct=True, method="average")

    # Long top-q (high rank → recent winners, expected to keep winning).
    # Short bottom-q (low rank → recent losers, expected to keep losing).
    pos = pd.DataFrame(0.0, index=pct_rank.index, columns=pct_rank.columns)
    pos = pos.where(~(pct_rank >= 1 - long_q), 1.0)
    if not long_only:
        pos = pos.where(~(pct_rank <= short_q), -1.0)

    pos = pos.where(rets.notna(), 0.0)
    pos = pos.shift(1).fillna(0.0)

    pos.index.name = "timestamp"
    pos.columns.name = "symbol"
    out = pos.stack().rename("position").reset_index()
    return out[["timestamp", "symbol", "position"]]
