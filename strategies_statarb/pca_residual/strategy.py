"""pca_residual — multi-asset PCA-residual mean reversion.

Hypothesis: on a basket of liquid USDT-perps the first ~3 principal
components by log-price are "BTC factor", "alt-sector factor", and a
third (volatility / meme / AI sector). Residuals from projection onto
those PCs are idiosyncratic component price series that should
mean-revert when they deviate.

For each rebalance:
  1. Build log(close) wide panel on the train slice.
  2. Standardize each column (subtract per-column mean, divide by std).
  3. PCA → K components.
  4. For each candidate symbol s:
       residual_s = standardized_s − reconstruction_s
       skip if ADF p-value > threshold or half-life > max
       compute implied weights on log(close) such that
            basket_spread = Σ_j w_{s,j} · log(close_j)  ≈  σ_s · residual_s + const
       truncate to top-K legs by |w|, renormalize (target symbol weight = 1)
  5. Sort by ADF p-value, take top n_baskets_target candidates.

Trading: rolling z-score of basket_spread, state-machine entry/exit.

Multi-symbol baskets share legs — the harness_statarb engine sums
per-symbol contributions across baskets and feeds the aggregated
position panel through the normal vbt path. With reasonable
`n_baskets_target` (e.g. 5) and `top_k_legs` (e.g. 6), gross exposure
stays well below the 100% vbt cap.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from harness_statarb.structures import (
    Basket,
    adf_pvalue,
    ou_half_life,
    pca_decompose,
)


# Top-30 USDT-perp universe with full 2024-01 → 2026-04 coverage on disk.
DEFAULT_SYMBOLS: list[str] = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "TRXUSDT",
    "BCHUSDT", "NEARUSDT", "ATOMUSDT", "LTCUSDT", "OPUSDT",
    "INJUSDT", "FILUSDT", "UNIUSDT", "SUIUSDT", "ICPUSDT",
    "ETCUSDT", "HBARUSDT", "SANDUSDT", "LDOUSDT", "XLMUSDT",
    "TIAUSDT", "SEIUSDT", "TONUSDT", "1000PEPEUSDT", "IMXUSDT",
]

DEFAULT_TF: str = "1h"

DEFAULT_PARAMS: dict = {
    # Engine knobs.
    "refit_freq_bars": 168,        # 7d on 1h
    "fit_window_bars": 2160,       # 90d on 1h
    "n_baskets_target": 5,
    "corr_threshold": 0.7,
    "retire_adf_pvalue": 0.20,
    # PCA / structure knobs.
    "n_components": 3,
    "fit_adf_pvalue_max": 0.10,
    "max_half_life_bars": 84,       # ≤ 0.5 · refit_freq → spread can revert before refit
    "top_k_legs": 6,                # keep top-6 weights per basket; saves on fee drag
    # Trading knobs.
    "z_window": 168,
    "entry_k": 2.0,
    "exit_k": 0.5,
    "hard_stop_k": 4.0,
}

PARAM_SPACE: dict = {
    "refit_freq_bars": (24, 720),
    "fit_window_bars": (720, 4320),
    "n_components": (1, 8),
    "n_baskets_target": (1, 15),
    "fit_adf_pvalue_max": (0.01, 0.30),
    "max_half_life_bars": (12, 240),
    "top_k_legs": (3, 30),
    "z_window": (48, 720),
    "entry_k": (1.0, 4.0),
    "exit_k": (0.0, 1.5),
    "hard_stop_k": (3.0, 8.0),
}


RAW_SIZING = True
MAX_POSITION = 2.0


def _implied_log_price_weights(
    components: np.ndarray,       # (K, n_symbols)
    scale: pd.Series,             # std per symbol (positive)
    symbol_index: int,
) -> np.ndarray:
    """For symbol s = `symbol_index`, return weights on log(close) such that
    Σ_j w_j · log(close_j) ≈ σ_s · standardized_residual_s + const.

    Math (see module docstring):
      residual_s_in_log_units = (log_s − μ_s) − Σ_j A_{j,s} · σ_s · (log_j − μ_j) / σ_j
      where A = components.T @ components  (n_symbols × n_symbols projector)
    So weights on log_j:
      w_{s,s} = 1 − A_{s,s}
      w_{s,j} = − A_{j,s} · σ_s / σ_j     for j ≠ s
    """
    A = components.T @ components       # (n_symbols, n_symbols)
    n = A.shape[0]
    s = symbol_index
    sigma = scale.values
    w = np.zeros(n)
    for j in range(n):
        if j == s:
            w[j] = 1.0 - A[s, s]
        else:
            sj = sigma[j] if sigma[j] != 0 else 1.0
            w[j] = -A[j, s] * sigma[s] / sj
    return w


def find_structures(train_data: dict, params: dict) -> list[Basket]:
    n_components = int(params.get("n_components", 3))
    n_baskets_target = int(params.get("n_baskets_target", 5))
    p_max = float(params.get("fit_adf_pvalue_max", 0.10))
    hl_max = float(params.get("max_half_life_bars", 84))
    top_k = int(params.get("top_k_legs", 6))

    frames = {s: df for s, df in train_data.items() if df is not None and not df.empty}
    if len(frames) < n_components + 2:
        return []
    # Align log-prices on common timestamps. Drop columns with too many NaNs.
    log_close = pd.concat({s: np.log(df["close"]) for s, df in frames.items()}, axis=1)
    log_close = log_close.dropna(how="any")
    if log_close.shape[0] < 50 or log_close.shape[1] < n_components + 2:
        return []
    pca = pca_decompose(log_close, n_components=n_components, standardize=True)
    components = pca["components"]
    scale = pca["scale"]
    residuals = pca["residuals"]                  # in original units (not standardized)
    if residuals.empty:
        return []

    # Score each candidate: ADF + half-life. Keep only those passing both.
    candidates = []
    for i, sym in enumerate(log_close.columns):
        res = residuals[sym].dropna()
        if len(res) < 50:
            continue
        p = adf_pvalue(res)
        if p > p_max:
            continue
        hl = ou_half_life(res)
        if not np.isfinite(hl) or hl > hl_max:
            continue
        candidates.append((p, hl, sym, i))

    candidates.sort(key=lambda x: x[0])           # lowest p-value first
    if not candidates:
        return []

    baskets: list[Basket] = []
    fit_end_ts = pd.Timestamp(log_close.index.max()).strftime("%Y%m%d%H%M")
    for p, hl, sym, sym_idx in candidates[:n_baskets_target]:
        w_full = _implied_log_price_weights(components, scale, sym_idx)
        # Truncate to top-K weights by absolute value (always keep target symbol).
        abs_w = np.abs(w_full)
        ranked = np.argsort(-abs_w)
        kept = set(ranked[:top_k].tolist())
        kept.add(sym_idx)
        legs = {}
        for j, w in enumerate(w_full):
            if j in kept and abs(w) > 1e-9:
                col = log_close.columns[j]
                legs[col] = float(w)
        if len(legs) < 2:
            continue
        b = Basket(
            id=f"PCA_{sym}@{fit_end_ts}",
            legs=legs,
            fit_stats={
                "adf_pvalue": float(p),
                "half_life": float(hl),
                "n_obs": int(len(log_close)),
                "n_legs_full": int(len(w_full)),
                "n_legs_kept": int(len(legs)),
                "n_components": n_components,
                "explained_variance": float(pca["explained"].sum()),
                "target_symbol": sym,
                "fit_window_start": str(log_close.index.min()),
                "fit_window_end": str(log_close.index.max()),
            },
        )
        baskets.append(b)
    return baskets


def trade_basket(
    basket: Basket,
    data: dict,
    params: dict,
    active_window: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> pd.Series:
    """Z-score state machine on the basket spread."""
    z_window = int(params.get("z_window", 168))
    entry_k = float(params.get("entry_k", 2.0))
    exit_k = float(params.get("exit_k", 0.5))
    hard_stop_k = float(params.get("hard_stop_k", 4.0))

    parts = []
    for sym, w in basket.legs.items():
        df = data.get(sym)
        if df is None or df.empty:
            continue
        parts.append(w * np.log(df["close"]))
    if len(parts) < 2:
        return pd.Series(dtype=float)
    spread = pd.concat(parts, axis=1).dropna().sum(axis=1)
    if spread.empty:
        return pd.Series(dtype=float)

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
                cur = 1.0
            elif v > entry_k:
                cur = -1.0
        else:
            if abs(v) > hard_stop_k or abs(v) < exit_k:
                cur = 0.0
            elif cur > 0 and v > entry_k:
                cur = -1.0
            elif cur < 0 and v < -entry_k:
                cur = 1.0
        state[i] = cur
    pos = pd.Series(state, index=z.index).shift(1).fillna(0.0)
    return pos
