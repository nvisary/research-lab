"""Cross-sectional momentum baseline.

Score each symbol by N-day return, rank, long top decile / short bottom decile,
equal weight, rebalance every bar at the decision TF.

Symbols are chosen from a fixed candidate list (DEFAULT_SYMBOLS). Ranking is
done bar-by-bar on the closes the harness provides. Lookahead-safe via
.shift(1) on the score before ranking.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# A reasonable starting universe of liquid majors+midcaps from the
# launched-before-2024 cohort. The agent may add/remove symbols.
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "ADAUSDT",
    "LTCUSDT", "TRXUSDT", "MATICUSDT", "ATOMUSDT", "NEARUSDT",
    "FILUSDT", "AAVEUSDT", "ETCUSDT", "BCHUSDT", "UNIUSDT",
    "SUIUSDT", "INJUSDT", "FTMUSDT", "ARBUSDT", "OPUSDT",
    "APTUSDT", "RUNEUSDT", "GALAUSDT", "AXSUSDT", "TIAUSDT",
]
DEFAULT_TF = "1d"

DEFAULT_PARAMS = {
    "lookback_bars": 30,    # momentum score = pct_change over this many bars
    "long_pct": 0.20,        # long top 20% of universe
    "short_pct": 0.20,       # short bottom 20%
    "skip_recent": 0,        # skip most-recent N bars in score (Asness "skip-month")
}

PARAM_SPACE = {
    "lookback_bars": (5, 90),
    "long_pct": (0.05, 0.40),
    "short_pct": (0.05, 0.40),
    "skip_recent": (0, 7),
}


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    lookback = int(params["lookback_bars"])
    long_pct = float(params["long_pct"])
    short_pct = float(params["short_pct"])
    skip = int(params.get("skip_recent", 0))

    # Build wide close frame. Symbols missing data (delisted / not yet listed)
    # contribute NaN rows; rank skips them via min_count.
    close_wide = pd.concat(
        {sym: df["close"] for sym, df in data.items() if not df.empty},
        axis=1,
    ).sort_index()
    if close_wide.empty:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    # Momentum score = (price_{t-skip} / price_{t-skip-lookback}) - 1
    score = close_wide.shift(skip) / close_wide.shift(skip + lookback) - 1.0
    # Lookahead guard: rank using yesterday's score for today's positions.
    score = score.shift(1)

    # Cross-sectional rank per bar (NaN-safe; symbols without data don't rank).
    ranks = score.rank(axis=1, pct=True, na_option="keep")

    longs = (ranks >= 1.0 - long_pct).astype(float)
    shorts = (ranks <= short_pct).astype(float)
    n_long = longs.sum(axis=1).replace(0, np.nan)
    n_short = shorts.sum(axis=1).replace(0, np.nan)
    # Equal weight inside each leg, sum to ±1 across the long basket and ∓1
    # across the short basket. Per-symbol position is 1/n_long for longs,
    # -1/n_short for shorts.
    pos = (longs.div(n_long, axis=0)).fillna(0.0) \
        - (shorts.div(n_short, axis=0)).fillna(0.0)

    # Long format expected by the harness.
    out = pos.stack().reset_index()
    out.columns = ["timestamp", "symbol", "position"]
    return out[out["position"] != 0.0]
