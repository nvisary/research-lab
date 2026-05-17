"""Bootstrap-based null-distribution p-values for strategy metrics.

`harness/stats.py` already has a *confidence interval* via stationary block
bootstrap. This module is the complementary tool: a **p-value under the null
hypothesis of zero edge** for Sharpe / Sortino / total-return, plus a
non-parametric null for maximum drawdown (lower one-sided: "is observed DD
better — i.e. smaller magnitude — than chance?").

Two flavours, both fast (<1s for ~5k bars × 1000 resamples):

* `block_bootstrap_pvalues` — stationary block bootstrap with mean
  re-centering. Resample the OOS-return series under the constraint
  E[r] = 0 (so the null is "same volatility/skew/kurt as observed, but
  zero drift"). For each resample compute Sharpe/Sortino/total-return,
  build the null distribution, p-value = P(metric_null >= metric_obs).

* `permutation_pvalues` — random shuffle of OOS-returns (i.i.d. null,
  destroys serial correlation). Cheap sanity check. p-value under the
  null "the realised P&L is just a permutation of equally-likely
  outcomes" — does not preserve volatility clustering, so it's
  *more* generous than block bootstrap. We report both; a strategy
  with p_perm << p_block survives random reordering but might be
  living off a single sustained run-up.

For DD: we use the same resample stream, recompute cumulative equity,
take the worst peak-to-trough, and ask P(DD_null <= DD_obs in
magnitude). Smaller-DD-is-better, so the p-value answers "how often
does a null path achieve a DD this shallow or shallower?"

This module is intentionally small and dependency-free beyond numpy/pandas
so it can be called per-iter without slowing the loop noticeably.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from harness.metrics import _resolve_periods_per_year


# --------------------------------------------------------------------------- #
@dataclass
class BootstrapResult:
    """Per-metric null-distribution stats. All metrics are annualized
    Sharpe / Sortino / fractional total-return; max_dd is fractional
    (positive number = magnitude of drawdown).

    p_sharpe / p_sortino / p_total_return: one-sided P(null >= observed).
    p_max_dd: one-sided P(null <= observed) (smaller-is-better).

    The `*_null_*` arrays are summary stats of the null distribution
    suitable for plotting a histogram in the UI — full draws are dropped
    after summarization to keep history.jsonl compact.
    """
    n_boot: int
    block_size: int
    method: str                         # "block" | "permutation"

    observed_sharpe: float
    observed_sortino: float
    observed_total_return: float
    observed_max_dd: float              # magnitude, positive

    p_sharpe: float
    p_sortino: float
    p_total_return: float
    p_max_dd: float

    null_sharpe_mean: float
    null_sharpe_std: float
    null_sharpe_quantiles: list[float]   # [p05, p25, p50, p75, p95]

    null_dd_mean: float
    null_dd_quantiles: list[float]

    # Compact histogram for the UI (bin edges + counts). 30 bins on Sharpe.
    null_sharpe_hist_edges: list[float]
    null_sharpe_hist_counts: list[int]

    def to_dict(self) -> dict:
        return {
            "n_boot": self.n_boot,
            "block_size": self.block_size,
            "method": self.method,
            "observed": {
                "sharpe": self.observed_sharpe,
                "sortino": self.observed_sortino,
                "total_return": self.observed_total_return,
                "max_dd": self.observed_max_dd,
            },
            "p_values": {
                "sharpe": self.p_sharpe,
                "sortino": self.p_sortino,
                "total_return": self.p_total_return,
                "max_dd": self.p_max_dd,
            },
            "null_sharpe": {
                "mean": self.null_sharpe_mean,
                "std": self.null_sharpe_std,
                "quantiles_05_25_50_75_95": self.null_sharpe_quantiles,
                "hist_edges": self.null_sharpe_hist_edges,
                "hist_counts": self.null_sharpe_hist_counts,
            },
            "null_max_dd": {
                "mean": self.null_dd_mean,
                "quantiles_05_25_50_75_95": self.null_dd_quantiles,
            },
        }


# --------------------------------------------------------------------------- #
def _stationary_block_indices(n: int, block_size: int,
                              rng: np.random.Generator) -> np.ndarray:
    """One stationary-bootstrap (Politis-Romano) index draw of length n.

    Geometric block lengths with mean = block_size; wraps modulo n.
    """
    if block_size <= 1:
        return rng.integers(0, n, size=n)
    out = np.empty(n, dtype=np.int64)
    pos = 0
    while pos < n:
        start = int(rng.integers(0, n))
        length = int(rng.geometric(1.0 / block_size))
        length = min(length, n - pos)
        for j in range(length):
            out[pos + j] = (start + j) % n
        pos += length
    return out


def _equity_max_dd_magnitude(returns: np.ndarray) -> float:
    """Magnitude of worst peak-to-trough on equity = cumprod(1 + r).

    Positive number; 0.10 = 10% drawdown.
    """
    if len(returns) == 0:
        return 0.0
    eq = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return float(-dd.min())  # convert to positive magnitude


def _sharpe_ann(returns: np.ndarray, ann_factor: float) -> float:
    if len(returns) < 2:
        return 0.0
    sd = returns.std(ddof=0)
    if sd <= 0:
        return 0.0
    return float(returns.mean() / sd * ann_factor)


def _sortino_ann(returns: np.ndarray, ann_factor: float) -> float:
    if len(returns) < 2:
        return 0.0
    downside = returns[returns < 0]
    if len(downside) == 0 or downside.std(ddof=0) <= 0:
        return 0.0
    return float(returns.mean() / downside.std(ddof=0) * ann_factor)


# --------------------------------------------------------------------------- #
def block_bootstrap_pvalues(returns: pd.Series,
                            n_boot: int = 1000,
                            block_size: int | None = None,
                            seed: int | None = 42,
                            tf: str | None = None) -> BootstrapResult | None:
    """Null-distribution p-values via stationary block bootstrap.

    Null = same returns distribution but **re-centered to zero mean**.
    This preserves variance, skew, kurtosis, and serial correlation
    (within block_size), so the resulting Sharpe distribution is the
    "no edge" benchmark given the realised volatility regime — a more
    honest null than i.i.d. permutation.

    Returns None if the sample is too small (< 30 observations).
    """
    r = returns.dropna().values.astype(float)
    n = len(r)
    if n < 30:
        return None
    if block_size is None:
        block_size = max(2, int(round(n ** (1 / 3))))

    # Re-center so the null literally has zero mean.
    centered = r - r.mean()

    ann = math.sqrt(_resolve_periods_per_year(returns.dropna().index, tf))
    obs_sharpe = _sharpe_ann(r, ann)
    obs_sortino = _sortino_ann(r, ann)
    obs_total = float(np.prod(1.0 + r) - 1.0)
    obs_dd = _equity_max_dd_magnitude(r)

    rng = np.random.default_rng(seed)
    null_sh = np.empty(n_boot)
    null_so = np.empty(n_boot)
    null_tr = np.empty(n_boot)
    null_dd = np.empty(n_boot)
    for i in range(n_boot):
        idx = _stationary_block_indices(n, block_size, rng)
        s = centered[idx]
        null_sh[i] = _sharpe_ann(s, ann)
        null_so[i] = _sortino_ann(s, ann)
        null_tr[i] = float(np.prod(1.0 + s) - 1.0)
        null_dd[i] = _equity_max_dd_magnitude(s)

    # Smoothed p-values: (#null >= obs + 1) / (n_boot + 1)
    p_sh = float((np.sum(null_sh >= obs_sharpe) + 1) / (n_boot + 1))
    p_so = float((np.sum(null_so >= obs_sortino) + 1) / (n_boot + 1))
    p_tr = float((np.sum(null_tr >= obs_total) + 1) / (n_boot + 1))
    p_dd = float((np.sum(null_dd <= obs_dd) + 1) / (n_boot + 1))

    q = [0.05, 0.25, 0.50, 0.75, 0.95]
    null_sh_q = [float(np.quantile(null_sh, x)) for x in q]
    null_dd_q = [float(np.quantile(null_dd, x)) for x in q]

    # Histogram for the UI. Bins chosen to bracket both obs and null.
    lo = float(min(null_sh.min(), obs_sharpe))
    hi = float(max(null_sh.max(), obs_sharpe))
    if hi - lo < 1e-9:
        hi = lo + 1e-9
    counts, edges = np.histogram(null_sh, bins=30, range=(lo, hi))

    return BootstrapResult(
        n_boot=n_boot,
        block_size=block_size,
        method="block",
        observed_sharpe=obs_sharpe,
        observed_sortino=obs_sortino,
        observed_total_return=obs_total,
        observed_max_dd=obs_dd,
        p_sharpe=p_sh,
        p_sortino=p_so,
        p_total_return=p_tr,
        p_max_dd=p_dd,
        null_sharpe_mean=float(null_sh.mean()),
        null_sharpe_std=float(null_sh.std(ddof=0)),
        null_sharpe_quantiles=null_sh_q,
        null_dd_mean=float(null_dd.mean()),
        null_dd_quantiles=null_dd_q,
        null_sharpe_hist_edges=[float(x) for x in edges],
        null_sharpe_hist_counts=[int(x) for x in counts],
    )


def permutation_pvalues(returns: pd.Series,
                        n_boot: int = 1000,
                        seed: int | None = 42,
                        tf: str | None = None) -> BootstrapResult | None:
    """Random-shuffle null (i.i.d. permutation, destroys autocorrelation).

    Cheaper and more generous than block bootstrap — strategies whose
    edge survives random reordering have signal that doesn't depend on
    the chronology of returns (rare in trend-following, common in
    mean-reversion). Compare p_perm vs p_block to see whether the
    strategy "lives" off temporal structure.

    Mean is NOT re-centered here: the permutation distribution preserves
    the empirical mean exactly (by construction), so it's not a strict
    H0:zero-edge test — it's H0:exchangeable. Use it as a robustness
    check on top of block_bootstrap_pvalues, which IS the zero-edge
    null.
    """
    r = returns.dropna().values.astype(float)
    n = len(r)
    if n < 30:
        return None
    ann = math.sqrt(_resolve_periods_per_year(returns.dropna().index, tf))
    obs_sharpe = _sharpe_ann(r, ann)
    obs_sortino = _sortino_ann(r, ann)
    obs_total = float(np.prod(1.0 + r) - 1.0)
    obs_dd = _equity_max_dd_magnitude(r)

    rng = np.random.default_rng(seed)
    null_sh = np.empty(n_boot)
    null_so = np.empty(n_boot)
    null_tr = np.empty(n_boot)
    null_dd = np.empty(n_boot)
    for i in range(n_boot):
        s = rng.permutation(r)
        null_sh[i] = _sharpe_ann(s, ann)
        null_so[i] = _sortino_ann(s, ann)
        null_tr[i] = float(np.prod(1.0 + s) - 1.0)
        null_dd[i] = _equity_max_dd_magnitude(s)

    p_sh = float((np.sum(null_sh >= obs_sharpe) + 1) / (n_boot + 1))
    p_so = float((np.sum(null_so >= obs_sortino) + 1) / (n_boot + 1))
    p_tr = float((np.sum(null_tr >= obs_total) + 1) / (n_boot + 1))
    p_dd = float((np.sum(null_dd <= obs_dd) + 1) / (n_boot + 1))

    q = [0.05, 0.25, 0.50, 0.75, 0.95]
    null_sh_q = [float(np.quantile(null_sh, x)) for x in q]
    null_dd_q = [float(np.quantile(null_dd, x)) for x in q]
    lo = float(min(null_sh.min(), obs_sharpe))
    hi = float(max(null_sh.max(), obs_sharpe))
    if hi - lo < 1e-9:
        hi = lo + 1e-9
    counts, edges = np.histogram(null_sh, bins=30, range=(lo, hi))

    return BootstrapResult(
        n_boot=n_boot,
        block_size=1,
        method="permutation",
        observed_sharpe=obs_sharpe,
        observed_sortino=obs_sortino,
        observed_total_return=obs_total,
        observed_max_dd=obs_dd,
        p_sharpe=p_sh,
        p_sortino=p_so,
        p_total_return=p_tr,
        p_max_dd=p_dd,
        null_sharpe_mean=float(null_sh.mean()),
        null_sharpe_std=float(null_sh.std(ddof=0)),
        null_sharpe_quantiles=null_sh_q,
        null_dd_mean=float(null_dd.mean()),
        null_dd_quantiles=null_dd_q,
        null_sharpe_hist_edges=[float(x) for x in edges],
        null_sharpe_hist_counts=[int(x) for x in counts],
    )


def both_pvalues(returns: pd.Series, *, n_boot: int = 1000,
                 tf: str | None = None,
                 seed: int | None = 42) -> dict | None:
    """Convenience: run both nulls and return a flat dict for persistence.

    Returns None if sample too small. Persisted into history.jsonl per
    iter so the UI can plot the null-distribution histogram and the
    operator can scan p-values at a glance.
    """
    block = block_bootstrap_pvalues(returns, n_boot=n_boot, tf=tf, seed=seed)
    if block is None:
        return None
    perm = permutation_pvalues(returns, n_boot=n_boot, tf=tf, seed=seed)
    out = {
        "block": block.to_dict(),
        "permutation": perm.to_dict() if perm else None,
    }
    return out
