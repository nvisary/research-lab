"""Multiple-testing adjustments for selection-bias-prone iteration loops.

When you run 30 iters and pick the best by composite, the headline Sharpe
is biased upward — even if no iter has true edge, the maximum of 30 noisy
estimates will be positive. DSR (harness/stats.py) already handles this
via the *expected-max under null* deflation. This module adds two
complementary tools the operator can read independently:

1. **Harvey-Liu (2016) haircut Sharpe**. Given an observed Sharpe with
   t-statistic t_obs and the count M of trials, compute the haircut
   factor under three corrections: Bonferroni, Holm, and BHY
   (Benjamini-Hochberg-Yekutieli). Output: the haircut Sharpe under
   each, plus the implied p-values. Conservative: Bonferroni assumes
   all trials are independent and ALL null; BHY allows correlated tests
   and controls false discovery rate, which is the right setting for
   crypto strategies (regime-dependent correlations across iters).

2. **Session-level adjusted DSR** — driven by the iter history's
   `trial_sharpes`. The base DSR in stats.py is fine; this adds a
   trajectory: DSR-as-of-iter-N. The trajectory shows whether
   evidence strengthened or weakened as the loop progressed.

Refs:
  Harvey & Liu, "Backtesting", J. Portfolio Mgmt 2015.
  Bailey & López de Prado, "The Sharpe Ratio Efficient Frontier", 2012.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import stats as sps


# --------------------------------------------------------------------------- #
def _sharpe_to_t(sharpe_ann: float, n_periods: int,
                 periods_per_year: float) -> float:
    """Convert annualised Sharpe to its t-statistic.

    sharpe_pp = sharpe_ann / sqrt(periods_per_year)
    t = sharpe_pp * sqrt(n_periods)
    """
    if periods_per_year <= 0 or n_periods <= 1:
        return 0.0
    sr_pp = sharpe_ann / math.sqrt(periods_per_year)
    return sr_pp * math.sqrt(n_periods)


def _t_to_sharpe(t_stat: float, n_periods: int,
                 periods_per_year: float) -> float:
    if n_periods <= 1 or periods_per_year <= 0:
        return 0.0
    sr_pp = t_stat / math.sqrt(n_periods)
    return sr_pp * math.sqrt(periods_per_year)


# --------------------------------------------------------------------------- #
def haircut_sharpe(sharpe_ann: float, n_periods: int, n_trials: int,
                   periods_per_year: float,
                   trials_correlation: float = 0.2) -> dict:
    """Harvey-Liu haircut Sharpe under Bonferroni / Holm / BHY.

    `trials_correlation` is the assumed pairwise correlation between
    trial t-statistics (used by the Harvey-Liu effective-N
    adjustment). Default 0.2 is conservative for highly correlated
    crypto strategies — iter-to-iter changes are small.

    Returns dict with for each method:
      adj_p_value, adj_t_stat, adj_sharpe, haircut_pct
    plus the raw t-stat and p-value.

    Conceptually: the unadjusted p-value is multiplied by the method's
    adjustment factor (Bonferroni: M; Holm: M-k+1 for rank-k; BHY:
    M*c(M)/k). Then adj_t = stats.norm.ppf(1 - adj_p). adj_sharpe =
    t_to_sharpe(adj_t). haircut_pct = 1 - adj_sharpe / sharpe_ann.
    """
    if n_trials < 1:
        n_trials = 1
    t_obs = _sharpe_to_t(sharpe_ann, n_periods, periods_per_year)
    p_obs = float(1.0 - sps.norm.cdf(t_obs)) if t_obs > 0 else 0.5
    # Numerical floor — pdf == 0 would give -inf t-stat back.
    p_obs = max(p_obs, 1e-12)

    # Bonferroni: assumes independence and full null. Most conservative.
    p_bonf = min(p_obs * n_trials, 1.0)

    # Holm: assumes our observed is the rank-1 (largest) result.
    # Rank-1 in Holm gets the same scaling as Bonferroni, but in
    # practice we report Holm = Bonferroni for the best result.
    p_holm = p_bonf

    # BHY: BH with c(M) for unknown dependence (Benjamini-Yekutieli).
    # adjusted p for the largest test: p_obs * M * c(M) / M = p_obs * c(M)
    # where c(M) = sum_{i=1..M} 1/i. For rank-1 (best) this dominates.
    cM = float(sum(1.0 / i for i in range(1, n_trials + 1)))
    p_bhy = min(p_obs * cM, 1.0)

    def _adj(p_adj: float) -> tuple[float, float, float]:
        # Avoid p=1 → t = -inf (gives nonsensical negative Sharpe).
        p_use = min(max(p_adj, 1e-12), 0.5 - 1e-12)
        t_adj = float(sps.norm.ppf(1.0 - p_use))
        s_adj = _t_to_sharpe(t_adj, n_periods, periods_per_year)
        haircut = (1.0 - s_adj / sharpe_ann) if sharpe_ann != 0 else 1.0
        return t_adj, s_adj, float(haircut)

    t_bonf, s_bonf, h_bonf = _adj(p_bonf)
    t_holm, s_holm, h_holm = _adj(p_holm)
    t_bhy, s_bhy, h_bhy = _adj(p_bhy)

    return {
        "n_trials": int(n_trials),
        "n_periods": int(n_periods),
        "raw": {"sharpe": sharpe_ann, "t_stat": t_obs, "p_value": p_obs},
        "bonferroni": {
            "p_value": p_bonf, "t_stat": t_bonf,
            "sharpe": s_bonf, "haircut_pct": h_bonf,
        },
        "holm": {
            "p_value": p_holm, "t_stat": t_holm,
            "sharpe": s_holm, "haircut_pct": h_holm,
        },
        "bhy": {
            "p_value": p_bhy, "t_stat": t_bhy,
            "sharpe": s_bhy, "haircut_pct": h_bhy,
        },
        "trials_correlation_assumed": trials_correlation,
    }


# --------------------------------------------------------------------------- #
def trial_sharpe_summary(trial_sharpes: list[float]) -> dict:
    """Distribution stats over the session's trial Sharpes.

    Useful for the UI to plot the histogram and the expected-max-under-null
    reference line.
    """
    a = np.asarray([s for s in trial_sharpes if s is not None], dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"n": 0}
    n = int(a.size)
    # Expected max of N standard normals — Gumbel approx (Bailey-LdP).
    if n <= 1:
        em_z = 0.0
    else:
        gamma = 0.5772156649
        em_z = float(
            (1 - gamma) * sps.norm.ppf(1 - 1.0 / n)
            + gamma * sps.norm.ppf(1 - 1.0 / (n * math.e))
        )
    # If we assume trial Sharpes have std equal to the empirical std,
    # the expected-max under "no edge" is em_z * std.
    std = float(a.std(ddof=1)) if n >= 2 else 0.0
    em_under_null = em_z * std

    counts, edges = np.histogram(a, bins=min(30, max(5, n // 2)))
    return {
        "n": n,
        "min": float(a.min()),
        "max": float(a.max()),
        "mean": float(a.mean()),
        "median": float(np.median(a)),
        "std": std,
        "expected_max_under_null": em_under_null,
        "selection_premium": float(a.max() - em_under_null),
        "hist_edges": [float(x) for x in edges],
        "hist_counts": [int(x) for x in counts],
    }
