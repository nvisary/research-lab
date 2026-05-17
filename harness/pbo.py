"""Overfit diagnostics: PBO-style metrics for single-strategy iteration.

The classical Bailey-Borwein-López de Prado PBO (2017) needs a
(strategies × time-splits) matrix of P&L: for each split, rank
strategies on IS, check the OOS rank of the IS-winner, accumulate
across splits → PBO = P(IS-best falls below OOS median).

We don't have many strategies in parallel — we have ONE strategy
that mutates over N iterations, plus an optional CPCV run that
gives us many train/test partitions of the same data for the
current strategy. So we provide two complementary diagnostics:

1. `cpcv_overfit_stats` — IS vs OOS Sharpe across CPCV paths for
   a fixed strategy. Even with one strategy, low/negative Spearman
   or low OLS slope between IS and OOS Sharpe indicates the
   strategy's IS performance does not predict OOS performance →
   the parameters are likely fit to the IS sample.

2. `session_overfit_stats` — IS vs OOS Sharpe across N iters of the
   research session. Each iter is a different strategy.py, but the
   train/OOS split is the same time partition for all. Strong
   monotonic IS↔OOS = the operator's edits are improving generalising
   alpha. Weak / inverted = the iter loop is fitting to OOS noise.
   When the IS-best iter's OOS rank is poor → selection bias is
   inflating reported best.

Both return dicts; the operator's UI can render them on the strategy
page. They are diagnostics — they DO NOT modify the keep/revert rule.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np


# --------------------------------------------------------------------------- #
def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation, NaN-safe. Falls back to 0 on degenerate."""
    if len(x) < 3:
        return 0.0
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    rx -= rx.mean()
    ry -= ry.mean()
    denom = np.sqrt((rx ** 2).sum() * (ry ** 2).sum())
    if denom <= 0:
        return 0.0
    return float((rx * ry).sum() / denom)


def _ols_slope(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Return (slope, intercept) of OLS y ~ a + b*x. (0,0) on degenerate."""
    if len(x) < 3:
        return 0.0, 0.0
    xc = x - x.mean()
    yc = y - y.mean()
    sxx = float((xc ** 2).sum())
    if sxx <= 0:
        return 0.0, float(y.mean())
    slope = float((xc * yc).sum() / sxx)
    intercept = float(y.mean() - slope * x.mean())
    return slope, intercept


def _logit_rank(rank: float, n: int) -> float:
    """Logit transform of a relative rank in (0, 1). Clipped to avoid inf."""
    eps = 1.0 / (2 * n)
    r = min(max(rank, eps), 1.0 - eps)
    return float(np.log(r / (1.0 - r)))


# --------------------------------------------------------------------------- #
def cpcv_overfit_stats(is_sharpes: Sequence[float],
                       oos_sharpes: Sequence[float]) -> dict:
    """Overfit stats for a CPCV run.

    Inputs are per-path IS and OOS annualised Sharpe (same length, same
    path order). For a healthy strategy these should be positively
    correlated — IS Sharpe ranks paths the same way OOS does.

    Returns:
      spearman_is_oos      — Spearman rho across paths. Healthy: > 0.3.
      slope_oos_on_is      — OLS slope. Healthy: > 0.3.
      intercept_oos        — intercept of the regression.
      pct_is_top_half_below_oos_median — fraction of paths in the
                              top-IS half that fall in the bottom OOS
                              half. Healthy: < 0.5. (Bailey-style
                              fragment.)
      logit_overfit        — log-odds of the IS-best path's OOS rank,
                              clipped. < 0 means IS-best is
                              actually above-median in OOS (healthy);
                              > 0 means IS-best fell below OOS median
                              (overfit signal).
      n_paths              — sample size.
    """
    x = np.asarray(is_sharpes, dtype=float)
    y = np.asarray(oos_sharpes, dtype=float)
    if len(x) != len(y) or len(x) < 4:
        return {
            "n_paths": int(len(x)),
            "spearman_is_oos": None,
            "slope_oos_on_is": None,
            "intercept_oos": None,
            "pct_is_top_half_below_oos_median": None,
            "logit_overfit": None,
        }

    rho = _spearman(x, y)
    slope, intercept = _ols_slope(x, y)

    is_median = float(np.median(x))
    oos_median = float(np.median(y))
    top_is_mask = x >= is_median
    n_top = int(top_is_mask.sum())
    pct_top_below = (float((y[top_is_mask] < oos_median).sum() / n_top)
                     if n_top > 0 else None)

    is_best_idx = int(np.argmax(x))
    # Relative OOS rank of the IS-best path: 1.0 = OOS-best (good),
    # 0.0 = OOS-worst (bad). Use "fraction of OOS values strictly less"
    # +0.5 mid-rank correction so ties are handled symmetrically.
    oos_rank_of_is_best = float(((y < y[is_best_idx]).sum() + 0.5) / len(y))
    # Bailey: λ = logit(F̄), F̄ = OOS rank of IS-best ∈ (0,1).
    # PBO is defined as P(λ < 0). We expose −λ so the user reads
    # "positive logit_overfit = IS-best fell below OOS median = overfit",
    # which matches the rest of our flag conventions where positive
    # numbers are bad-news.
    lam = _logit_rank(oos_rank_of_is_best, len(y))
    logit_overfit = -lam

    return {
        "n_paths": int(len(x)),
        "spearman_is_oos": rho,
        "slope_oos_on_is": slope,
        "intercept_oos": intercept,
        "pct_is_top_half_below_oos_median": pct_top_below,
        "logit_overfit": logit_overfit,
        "is_median_sharpe": is_median,
        "oos_median_sharpe": oos_median,
    }


# --------------------------------------------------------------------------- #
def session_overfit_stats(trial_train_sharpes: Sequence[float],
                          trial_oos_sharpes: Sequence[float]) -> dict:
    """Overfit stats over the full iteration session.

    Same idea as `cpcv_overfit_stats` but each datapoint is a different
    strategy.py version (an iter), evaluated on the SAME train/OOS time
    partition. Tracks whether the operator's edits are producing
    generalising alpha or fitting OOS noise.

    Adds two session-specific metrics:
      best_is_oos_gap — gap between the IS-best iter's OOS Sharpe and
        the median OOS Sharpe across iters. Large positive = the
        operator picked an OOS winner whose IS performance also led
        (consistent). Large negative = IS-best is OOS-mediocre — the
        keep/revert composite is driven mostly by OOS noise.
      selection_inflation — Sharpe of the (committed) best iter minus
        median Sharpe of all iters. Big inflation + many iters → DSR
        haircut should be material.
    """
    out = cpcv_overfit_stats(trial_train_sharpes, trial_oos_sharpes)
    out["n_iters"] = out.pop("n_paths")

    x = np.asarray(trial_train_sharpes, dtype=float)
    y = np.asarray(trial_oos_sharpes, dtype=float)
    if len(x) >= 4 and len(x) == len(y):
        is_best_idx = int(np.argmax(x))
        out["best_is_oos_gap"] = float(y[is_best_idx] - np.median(y))
        oos_best = float(y.max())
        out["selection_inflation"] = float(oos_best - np.median(y))
    else:
        out["best_is_oos_gap"] = None
        out["selection_inflation"] = None
    return out


# --------------------------------------------------------------------------- #
def overfit_flag(stats: dict) -> str:
    """One-line ✓/⚠/✗ verdict for the UI / iter summary.

    Healthy: spearman > 0.3 AND logit_overfit < 0.
    Suspect: spearman in [0, 0.3] OR 0 <= logit_overfit < 1.0.
    Bad:     spearman < 0 OR logit_overfit >= 1.0.
    """
    rho = stats.get("spearman_is_oos")
    logit = stats.get("logit_overfit")
    if rho is None or logit is None:
        return "ℹ insufficient data for overfit verdict"
    if rho > 0.3 and logit < 0:
        return f"✓ IS↔OOS ρ={rho:+.2f}, logit_overfit={logit:+.2f}"
    if rho < 0 or logit >= 1.0:
        return f"✗ IS↔OOS ρ={rho:+.2f}, logit_overfit={logit:+.2f} (overfit signal)"
    return f"⚠ IS↔OOS ρ={rho:+.2f}, logit_overfit={logit:+.2f}"
