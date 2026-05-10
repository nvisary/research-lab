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

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
    "DOTUSDT", "TRXUSDT", "BCHUSDT", "ETCUSDT", "NEARUSDT",
    "ATOMUSDT", "FILUSDT", "ICPUSDT", "UNIUSDT", "OPUSDT",
    "INJUSDT", "ARUSDT", "SUIUSDT", "TIAUSDT", "SEIUSDT",
    "HBARUSDT", "GRTUSDT", "IMXUSDT", "MNTUSDT", "RUNEUSDT",
    "ENSUSDT", "LDOUSDT", "GALAUSDT", "AXSUSDT", "BLURUSDT",
    "ORDIUSDT", "SANDUSDT", "THETAUSDT", "TONUSDT", "WLDUSDT",
    "XLMUSDT", "XMRUSDT", "NEOUSDT", "KASUSDT", "EGLDUSDT",
]
DEFAULT_TF = "1d"

DEFAULT_PARAMS = {
    "lookback": 30,
    "long_quantile": 0.3,
    "short_quantile": 0.3,
    "long_only": 0,
    "hold_days": 1,
    "vol_target": 1,
    "vol_window": 30,
}

PARAM_SPACE = {
    "lookback": (5, 180),
    "long_quantile": (0.1, 0.5),
    "short_quantile": (0.1, 0.5),
    "long_only": (0, 1),
    "hold_days": (1, 30),
    "vol_target": (0, 1),
    "vol_window": (10, 60),
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

    # C1: vol-target per leg — weight ∝ 1/sigma_i
    vol_target = bool(int(params.get("vol_target", 0)))
    vol_window = int(params.get("vol_window", 30))
    if vol_target and vol_window >= 5:
        daily = closes.pct_change()
        sigma = daily.rolling(vol_window, min_periods=vol_window // 2).std()
        sigma = sigma.replace(0, np.nan)
        inv_vol = 1.0 / sigma
        signed_w = pos.mul(inv_vol).where(pos != 0, 0.0)
        active = (pos != 0).sum(axis=1)
        gross = signed_w.abs().sum(axis=1).replace(0, np.nan)
        scale = active / gross
        pos = signed_w.mul(scale, axis=0).fillna(0.0)

    # A4: holding period
    hold_days = int(params.get("hold_days", 1))
    if hold_days > 1:
        idx = np.arange(len(pos))
        keep_mask = (idx % hold_days) == 0
        held = pos.where(pd.Series(keep_mask, index=pos.index), other=np.nan)
        pos = held.ffill().fillna(0.0)

    pos = pos.shift(1).fillna(0.0)

    pos.index.name = "timestamp"
    pos.columns.name = "symbol"
    out = pos.stack().rename("position").reset_index()
    return out[["timestamp", "symbol", "position"]]
