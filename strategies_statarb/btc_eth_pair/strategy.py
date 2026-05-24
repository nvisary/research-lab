"""btc_eth_pair — smoke-test stat-arb strategy.

Trivial baseline used to validate the harness_statarb engine end-to-end
before building real strategies. One basket only: BTC + β·(−ETH), with
β re-estimated by OLS on each refit. Trade z-score of the residual.

Hypothesis is not the point here — this exists so we can verify:
  1. find_structures + trade_basket plumbing produces non-empty signals
  2. RAW_SIZING decomposition into per-symbol positions yields plausible
     market-neutral exposure (long ~50%, short ~50%, net ~0)
  3. The composite / diagnostics machinery runs without crashing
  4. The lookahead audit passes
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from harness_statarb.structures import (
    Basket,
    adf_pvalue,
    engle_granger,
    ou_half_life,
)


DEFAULT_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT"]
DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    # Engine knobs (consumed by harness_statarb shim, not by find_structures).
    "refit_freq_bars": 168,        # 7d on 1h
    "fit_window_bars": 2160,       # 90d on 1h
    "n_baskets_target": 2,         # we only build 1 basket, but headroom is fine
    "corr_threshold": 0.95,        # not relevant for single basket
    "retire_adf_pvalue": 0.20,     # retire if spread loses stationarity (lenient)
    # Strategy knobs.
    "z_window": 168,               # 7d rolling baseline for z-score
    "entry_k": 2.0,
    "exit_k": 0.5,
    "hard_stop_k": 4.0,
    "fit_adf_pvalue_max": 0.20,    # accept a pair only if train residual ADF p < this
}

PARAM_SPACE: dict = {
    "refit_freq_bars": (24, 720),
    "fit_window_bars": (720, 4320),
    "z_window": (48, 720),
    "entry_k": (1.0, 4.0),
    "exit_k": (0.0, 1.5),
    "hard_stop_k": (3.0, 8.0),
    "fit_adf_pvalue_max": (0.01, 0.50),
}


# Stat-arb mode: positions emitted are fractions of total equity (the
# harness_statarb shim sets RAW_SIZING = True regardless, but we mirror
# it here for documentation).
RAW_SIZING = True
MAX_POSITION = 2.0


def find_structures(train_data: dict, params: dict) -> list[Basket]:
    """Fit a single BTC/ETH pair on the train slice. Returns 0 or 1 basket."""
    btc = train_data.get("BTCUSDT")
    eth = train_data.get("ETHUSDT")
    if btc is None or eth is None or btc.empty or eth.empty:
        return []
    y = np.log(btc["close"])
    x = np.log(eth["close"])
    fit = engle_granger(y, x, add_constant=True)
    beta = fit["beta"]
    if not np.isfinite(beta) or beta == 0.0:
        return []
    p_thresh = float(params.get("fit_adf_pvalue_max", 0.20))
    if fit["adf_pvalue"] > p_thresh:
        return []
    half_life = fit["half_life"]
    fit_end_ts = pd.Timestamp(y.index.max()).strftime("%Y%m%d%H%M")
    basket = Basket(
        id=f"BTC_ETH@{fit_end_ts}",
        legs={"BTCUSDT": 1.0, "ETHUSDT": -beta},
        fit_stats={
            "beta": float(beta),
            "alpha": float(fit["alpha"]),
            "adf_pvalue": float(fit["adf_pvalue"]),
            "half_life": float(half_life),
            "beta_stderr": float(fit["beta_stderr"]),
            "n_obs": int(fit["n_obs"]),
            "fit_window_start": str(y.index.min()),
            "fit_window_end": str(y.index.max()),
        },
    )
    return [basket]


def trade_basket(
    basket: Basket,
    data: dict,
    params: dict,
    active_window: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> pd.Series:
    """Z-score of the basket spread → state-machine entry/exit."""
    z_window = int(params.get("z_window", 168))
    entry_k = float(params.get("entry_k", 2.0))
    exit_k = float(params.get("exit_k", 0.5))
    hard_stop_k = float(params.get("hard_stop_k", 4.0))

    btc = data.get("BTCUSDT")
    eth = data.get("ETHUSDT")
    if btc is None or eth is None or btc.empty or eth.empty:
        return pd.Series(dtype=float)
    # Use the basket's actual normalized weights — after normalize_to_gross
    # they're no longer (1, -beta) but (w_btc, w_eth) with |w_btc|+|w_eth|=1.
    w_btc = basket.legs.get("BTCUSDT", 0.0)
    w_eth = basket.legs.get("ETHUSDT", 0.0)
    spread = (w_btc * np.log(btc["close"])).add(w_eth * np.log(eth["close"]), fill_value=0.0)
    spread = spread.dropna()
    mu = spread.rolling(z_window, min_periods=z_window).mean()
    sd = spread.rolling(z_window, min_periods=z_window).std()
    z = (spread - mu) / sd

    state = np.zeros(len(z), dtype=np.float64)
    cur = 0.0
    zv = z.to_numpy()
    for i in range(len(zv)):
        v = zv[i]
        if np.isnan(v):
            state[i] = 0.0
            cur = 0.0
            continue
        if cur == 0.0:
            if v < -entry_k:
                cur = 1.0       # long spread (long BTC, short ETH if w_btc>0)
            elif v > entry_k:
                cur = -1.0
        else:
            if abs(v) > hard_stop_k:
                cur = 0.0
            elif abs(v) < exit_k:
                cur = 0.0
            elif cur > 0 and v > entry_k:
                cur = -1.0
            elif cur < 0 and v < -entry_k:
                cur = 1.0
        state[i] = cur

    pos = pd.Series(state, index=z.index).shift(1).fillna(0.0)
    return pos
