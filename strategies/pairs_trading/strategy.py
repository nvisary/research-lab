"""Pairs trading on BTC/ETH log-spread.

Classic statistical-arbitrage pair: BTC and ETH are the two most
liquid Bybit perps and historically co-move tightly. We trade the
log-price spread, not directional moves:

    spread_t = log(BTC_t) - log(ETH_t)
    z_t      = (spread_t - mean_w(spread)) / std_w(spread)

When z dips to -z_thresh the spread is unusually narrow (BTC cheap
vs ETH) → long BTC, short ETH. When z rises to +z_thresh the spread
is unusually wide → short BTC, long ETH. Exit when |z| crosses
z_exit toward zero.

Sizing: RAW mode, 0.5 per leg. Sum |position| = 1.0 = full equity
deployed but market-neutral (50% long + 50% short of the same
notional). The harness cash_sharing cap permits this.

Hypothesis: BTC and ETH share a dominant risk factor (broad crypto
beta). Their log-spread is stationary on multi-day horizons; large
deviations are noise that mean-reverts. Edge survives funding only
when the spread mean-reverts faster than ~half the funding cycle.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DESCRIPTION = (
    "Pairs trading on BTC/ETH 1h log-spread. Rolling-week z-score "
    "of log(BTC)-log(ETH); when z<-2 long BTC / short ETH (spread "
    "narrow), when z>+2 short BTC / long ETH (spread wide), exit at "
    "z=0. Market-neutral by construction — 50% per leg, total "
    "exposure 100% but net beta ~0."
)

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DEFAULT_TF = "1h"

RAW_SIZING = True
MAX_POSITION = 1.0

DEFAULT_PARAMS = {
    "zwindow": 168,    # rolling window for spread mean/std (1 week of 1h bars)
    "z_thresh": 2.0,   # |z| > z_thresh required to enter
    "z_exit": 0.0,     # exit when z crosses this level toward zero
    "leg_size": 0.5,   # fraction of equity per leg
}

PARAM_SPACE = {
    "zwindow": (24, 720),
    "z_thresh": (1.0, 4.0),
    "z_exit": (-1.0, 1.0),
    "leg_size": (0.1, 0.5),
}


def _state_machine(z: np.ndarray, z_thresh: float, z_exit: float) -> np.ndarray:
    """Returns +1 = long spread (long A / short B), -1 = short spread, 0 = flat.

    Long the spread when z is far negative (spread below mean → expect rebound
    upward → long A, short B). Short the spread when z is far positive.
    """
    n = z.shape[0]
    out = np.zeros(n, dtype=np.float64)
    pos = 0
    for t in range(n):
        v = z[t]
        if not np.isfinite(v):
            out[t] = pos
            continue
        if pos > 0 and v >= z_exit:
            pos = 0
        elif pos < 0 and v <= -z_exit:
            pos = 0
        if pos == 0:
            if v <= -z_thresh:
                pos = 1
            elif v >= z_thresh:
                pos = -1
        out[t] = pos
    return out


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    zwindow = int(params.get("zwindow", 168))
    z_thresh = float(params.get("z_thresh", 2.0))
    z_exit = float(params.get("z_exit", 0.0))
    leg_size = float(params.get("leg_size", 0.5))

    if zwindow < 5 or z_thresh <= 0 or leg_size <= 0:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    if "BTCUSDT" not in data or "ETHUSDT" not in data:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    btc = data["BTCUSDT"]
    eth = data["ETHUSDT"]
    if btc.empty or eth.empty:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    idx = btc.index.intersection(eth.index)
    if len(idx) < zwindow + 5:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    log_btc = np.log(btc.loc[idx, "close"])
    log_eth = np.log(eth.loc[idx, "close"])
    spread = log_btc - log_eth

    m = spread.rolling(zwindow, min_periods=zwindow).mean()
    s = spread.rolling(zwindow, min_periods=zwindow).std(ddof=0)
    z = (spread - m) / s.replace(0, np.nan)

    direction = _state_machine(z.to_numpy(dtype=np.float64), z_thresh, z_exit)
    spread_pos = pd.Series(direction, index=idx).shift(1).fillna(0.0)

    btc_pos = spread_pos * leg_size
    eth_pos = -spread_pos * leg_size

    rows = [
        pd.DataFrame({"timestamp": idx, "symbol": "BTCUSDT", "position": btc_pos.values}),
        pd.DataFrame({"timestamp": idx, "symbol": "ETHUSDT", "position": eth_pos.values}),
    ]
    return pd.concat(rows, ignore_index=True)
