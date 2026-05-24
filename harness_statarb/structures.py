"""Structure-discovery primitives for stat-arb.

This module is a thin, opinionated wrapper layer on top of `statsmodels`
and `sklearn`. Strategies import these helpers from
`strategies_statarb/<name>/strategy.py` and use them inside
`find_structures(train_data, params)`.

All functions in this module are **pure** with respect to their inputs
and do NOT touch any data outside what is passed in. This is what makes
the stat-arb lookahead audit (`harness_statarb/lookahead.py`) tractable:
if `find_structures` uses only these helpers on the train slice, then
permuting bars outside the train slice cannot change its output.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen


# ---------------------------------------------------------------------------
# Basket type — the unit of stat-arb structure
# ---------------------------------------------------------------------------

@dataclass
class Basket:
    """A single stat-arb structure: a weighted combination of symbols.

    `legs` maps symbol → signed weight. Convention: weights are *price*
    coefficients (i.e. the spread is Σ_i w_i · price_i), not notional
    fractions. The backtest engine converts weights → per-leg notional
    when it allocates capital to the basket.

    `fit_stats` records diagnostic statistics computed at fit time;
    they are NOT used for trading decisions (those live inside the
    strategy's `trade_basket`). They drive `harness_statarb/diagnostics.py`
    flags and the stat-arb composite (half-life penalty).
    """

    id: str
    legs: dict[str, float]
    fit_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "legs": dict(self.legs),
            "fit_stats": dict(self.fit_stats),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Basket":
        return cls(
            id=d["id"],
            legs=dict(d["legs"]),
            fit_stats=dict(d.get("fit_stats", {})),
        )

    def normalize_to_gross(self, gross: float = 1.0) -> "Basket":
        """Return a copy with legs rescaled so Σ|w_i| == gross."""
        s = sum(abs(v) for v in self.legs.values())
        if s == 0.0:
            return Basket(id=self.id, legs=dict(self.legs), fit_stats=dict(self.fit_stats))
        scale = gross / s
        return Basket(
            id=self.id,
            legs={k: v * scale for k, v in self.legs.items()},
            fit_stats=dict(self.fit_stats),
        )


# ---------------------------------------------------------------------------
# Stationarity / mean-reversion tests
# ---------------------------------------------------------------------------

def adf_pvalue(series: pd.Series | np.ndarray, regression: str = "c") -> float:
    """Augmented Dickey-Fuller p-value (null: series has a unit root).

    Lower p → stronger evidence of stationarity → better stat-arb
    candidate. Typical threshold: p < 0.05.

    `regression`:
      "c"  — constant only (default)
      "ct" — constant + linear trend
      "n"  — no constant (centered series)
    """
    s = pd.Series(series).dropna()
    if len(s) < 20:
        return 1.0
    try:
        # autolag="AIC" chooses lag length; suppress UserWarning.
        result = adfuller(s.values, regression=regression, autolag="AIC")
        return float(result[1])
    except Exception:
        return 1.0


def ou_half_life(spread: pd.Series | np.ndarray) -> float:
    """Half-life of mean reversion under an OU model, in bars.

    Estimate by OLS:  Δspread_t = α + λ · spread_{t-1} + ε_t
    Half-life = -ln(2) / ln(1 + λ)   when λ ∈ (-1, 0).

    Returns +inf for non-mean-reverting series (λ ≥ 0) or when the fit
    is degenerate. A *low* finite half-life (e.g. < 20 bars on 1h) is
    what makes a basket tradable inside one refit cycle.
    """
    s = pd.Series(spread).dropna()
    if len(s) < 20:
        return float("inf")
    s = s.reset_index(drop=True)
    y = s.diff().iloc[1:].to_numpy()
    x = s.shift(1).iloc[1:].to_numpy()
    if np.std(x) == 0 or np.std(y) == 0:
        return float("inf")
    # OLS: y = α + λ x. We need just λ.
    A = np.column_stack([np.ones_like(x), x])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    lam = float(coef[1])
    if lam >= 0.0 or lam <= -1.0:
        return float("inf")
    return float(-np.log(2.0) / np.log1p(lam))


# ---------------------------------------------------------------------------
# Engle-Granger pair cointegration
# ---------------------------------------------------------------------------

def engle_granger(
    y: pd.Series,
    x: pd.Series,
    add_constant: bool = True,
) -> dict[str, Any]:
    """OLS hedge ratio for a pair: y = α + β·x + residual.

    Returns:
      {
        "beta":         hedge ratio β   (long y, short β·x → spread = y − β·x)
        "alpha":        intercept (only if add_constant)
        "residual":     pd.Series of spread (aligned to common index of x,y)
        "adf_pvalue":   ADF p-value on residual
        "half_life":    OU half-life of residual, in bars
        "beta_stderr":  standard error of β (OLS)
        "n_obs":        number of observations used in fit
      }

    The pair is a good cointegration candidate iff adf_pvalue is small
    AND half_life is finite and short relative to your refit cadence.
    """
    df = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
    if len(df) < 30:
        return {
            "beta": float("nan"),
            "alpha": float("nan"),
            "residual": pd.Series(dtype=float),
            "adf_pvalue": 1.0,
            "half_life": float("inf"),
            "beta_stderr": float("inf"),
            "n_obs": int(len(df)),
        }
    xv = df["x"].to_numpy()
    yv = df["y"].to_numpy()
    if add_constant:
        A = np.column_stack([np.ones_like(xv), xv])
    else:
        A = xv.reshape(-1, 1)
    coef, *_ = np.linalg.lstsq(A, yv, rcond=None)
    if add_constant:
        alpha, beta = float(coef[0]), float(coef[1])
        residual = yv - (alpha + beta * xv)
    else:
        alpha, beta = 0.0, float(coef[0])
        residual = yv - beta * xv
    # OLS standard error of β
    sse = float(np.sum(residual ** 2))
    df_resid = max(len(df) - (2 if add_constant else 1), 1)
    sigma2 = sse / df_resid
    var_x = float(np.var(xv, ddof=1))
    beta_stderr = float(np.sqrt(sigma2 / (var_x * (len(xv) - 1)))) if var_x > 0 else float("inf")
    res_series = pd.Series(residual, index=df.index, name="residual")
    return {
        "beta": beta,
        "alpha": alpha,
        "residual": res_series,
        "adf_pvalue": adf_pvalue(res_series),
        "half_life": ou_half_life(res_series),
        "beta_stderr": beta_stderr,
        "n_obs": int(len(df)),
    }


# ---------------------------------------------------------------------------
# Johansen cointegration (for n ≥ 2 assets)
# ---------------------------------------------------------------------------

def johansen(
    panel: pd.DataFrame,
    det_order: int = 0,
    k_ar_diff: int = 1,
    sig: float = 0.05,
) -> dict[str, Any]:
    """Johansen cointegration test on a panel of prices.

    `panel`: DataFrame, columns = symbols, rows = time, values =
             log(close) (or close — both work; logs are conventional).
    `det_order`: -1 (no det), 0 (constant), 1 (constant + trend).
    `k_ar_diff`: lag order on differences.
    `sig`: significance for "is_cointegrated" verdict (0.10, 0.05, 0.01).

    Returns:
      {
        "n_coint":       number of cointegrating relations at `sig`
        "vectors":       (n × n) array, columns = cointegrating vectors
                          (rank-ordered by eigenvalue, strongest first)
        "eigenvalues":   array of n eigenvalues
        "best_vector":   first cointegrating vector as dict[symbol, weight],
                          normalized so first symbol's weight == 1.0
                          (None if n_coint == 0)
        "best_residual": spread series for `best_vector` on the input panel
                          (None if n_coint == 0)
        "trace_stats":   trace statistics (n,)
        "trace_crits":   critical values at sig (n,)
      }
    """
    df = panel.dropna()
    n = df.shape[1]
    if n < 2 or len(df) < max(30, 5 * (k_ar_diff + 1)):
        return {
            "n_coint": 0,
            "vectors": np.zeros((n, n)),
            "eigenvalues": np.zeros(n),
            "best_vector": None,
            "best_residual": None,
            "trace_stats": np.zeros(n),
            "trace_crits": np.zeros(n),
        }
    sig_idx = {0.10: 0, 0.05: 1, 0.01: 2}.get(sig, 1)
    try:
        res = coint_johansen(df.values, det_order=det_order, k_ar_diff=k_ar_diff)
    except Exception:
        return {
            "n_coint": 0,
            "vectors": np.zeros((n, n)),
            "eigenvalues": np.zeros(n),
            "best_vector": None,
            "best_residual": None,
            "trace_stats": np.zeros(n),
            "trace_crits": np.zeros(n),
        }
    trace_stats = np.asarray(res.lr1)
    trace_crits = np.asarray(res.cvt[:, sig_idx])
    # n_coint = max r such that trace_stat[r] > crit[r] for all r' ≤ r
    n_coint = 0
    for r in range(n):
        if trace_stats[r] > trace_crits[r]:
            n_coint = r + 1
        else:
            break
    vectors = np.asarray(res.evec)            # columns are eigenvectors
    eigs = np.asarray(res.eig)
    best_vector = None
    best_residual = None
    if n_coint > 0:
        v = vectors[:, 0]
        # Normalize so the first non-zero weight is +1 (sign + scale convention).
        for i in range(n):
            if abs(v[i]) > 1e-12:
                v = v / v[i]
                break
        best_vector = {col: float(v[i]) for i, col in enumerate(df.columns)}
        spread = df.values @ v
        best_residual = pd.Series(spread, index=df.index, name="residual")
    return {
        "n_coint": int(n_coint),
        "vectors": vectors,
        "eigenvalues": eigs,
        "best_vector": best_vector,
        "best_residual": best_residual,
        "trace_stats": trace_stats,
        "trace_crits": trace_crits,
    }


# ---------------------------------------------------------------------------
# PCA residual decomposition
# ---------------------------------------------------------------------------

def pca_decompose(
    panel: pd.DataFrame,
    n_components: int,
    standardize: bool = True,
) -> dict[str, Any]:
    """PCA on a panel of (log-)prices or returns.

    `panel`: DataFrame, columns = symbols, rows = time. Caller decides
             whether to pass log-prices, returns, or standardized prices.
    `n_components`: number of principal components to extract.
    `standardize`: subtract per-symbol mean and divide by per-symbol std
                   before PCA (recommended for cross-asset).

    Returns:
      {
        "components":  (n_components × n_symbols) matrix — PC loadings
                       in symbol space. Row k is the k-th PC.
        "scores":      DataFrame (time × n_components) — PC time series.
        "residuals":   DataFrame (time × n_symbols) — what's left after
                       projection onto the top-k PCs.
        "explained":   array (n_components,) — variance explained ratios.
        "mean":        pd.Series (n_symbols,) — column means used.
        "scale":       pd.Series (n_symbols,) — column stds used (1 if not standardize).
      }
    """
    df = panel.dropna()
    if df.shape[1] < n_components or len(df) < n_components + 1:
        empty_df = pd.DataFrame(index=df.index)
        return {
            "components": np.zeros((n_components, df.shape[1])),
            "scores": pd.DataFrame(index=df.index),
            "residuals": empty_df,
            "explained": np.zeros(n_components),
            "mean": pd.Series(0.0, index=df.columns),
            "scale": pd.Series(1.0, index=df.columns),
        }
    mean = df.mean(axis=0)
    if standardize:
        scale = df.std(axis=0).replace(0.0, 1.0)
    else:
        scale = pd.Series(1.0, index=df.columns)
    centered = (df - mean) / scale
    pca = PCA(n_components=n_components)
    scores_arr = pca.fit_transform(centered.values)
    components = pca.components_                  # (n_components, n_symbols)
    # Reconstruction = scores @ components → in centered/scaled space.
    reconstruction = scores_arr @ components
    residual_arr = centered.values - reconstruction
    # Bring residual back to original scale (multiply by scale).
    residuals_unscaled = residual_arr * scale.values  # broadcast over columns
    return {
        "components": components,
        "scores": pd.DataFrame(
            scores_arr,
            index=df.index,
            columns=[f"PC{i+1}" for i in range(n_components)],
        ),
        "residuals": pd.DataFrame(residuals_unscaled, index=df.index, columns=df.columns),
        "explained": np.asarray(pca.explained_variance_ratio_),
        "mean": mean,
        "scale": scale,
    }


# ---------------------------------------------------------------------------
# Hedge ratio stability (rolling β std / mean)
# ---------------------------------------------------------------------------

def hedge_ratio_stability(
    y: pd.Series,
    x: pd.Series,
    window: int,
) -> float:
    """Coefficient of variation of rolling OLS β between y and x.

    Lower is better (β is stable across windows → cointegration relation
    is structurally persistent, not a fluke of the full-sample fit).
    Returns +inf if the rolling fit is degenerate.
    """
    df = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
    if len(df) < window * 2:
        return float("inf")
    yv = df["y"].to_numpy()
    xv = df["x"].to_numpy()
    betas = []
    for i in range(window, len(df) + 1):
        xw = xv[i - window:i]
        yw = yv[i - window:i]
        var_x = float(np.var(xw, ddof=1))
        if var_x == 0:
            continue
        cov_xy = float(np.cov(xw, yw, ddof=1)[0, 1])
        betas.append(cov_xy / var_x)
    if len(betas) < 3:
        return float("inf")
    arr = np.asarray(betas)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    if abs(mean) < 1e-12:
        return float("inf")
    return float(std / abs(mean))
