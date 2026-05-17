"""Multi-strategy hypothesis tests — formal answers to:

  Q1. *"Is the best of my N strategies actually better than zero,
       once I account for the fact that I picked the best of N?"*
  Q2. *"For each individual strategy, what is its FWER-controlled
       p-value against H0: mean return ≤ 0, given the joint
       distribution across all N strategies?"*

DSR (`harness/stats.py`) deflates a SINGLE strategy's Sharpe for the
number of iters in its own selection. That is **within-strategy**
multiple testing. This module covers **across-strategy** multiple
testing — when you have a stable of finished strategies and want to
honestly say "the winner survives the multiplicity penalty".

Three tests, all built on the **stationary block bootstrap** (Politis &
Romano 1994) of the joint [T × N] returns matrix so the joint
distribution of strategy returns (including their correlations) is
preserved under the null:

1. **White Reality Check (RC, 2000)** — pure test of the null
   "no strategy has positive mean return". Test stat: max over k of
   sqrt(T) · mean(r_k). Bootstrap null: same statistic on resampled,
   mean-recentered returns. p = P(null_max ≥ obs_max).
   Conservative when many "bad" strategies are in the universe —
   their negative-drift draws inflate the null max.

2. **Hansen SPA (2005)** — fixes RC's conservativeness via two
   adjustments: (a) **studentization** (each strategy's mean divided
   by its bootstrap std), so different-vol strategies are comparable;
   (b) **threshold recentering** — strategies whose observed mean is
   already far below zero are EXCLUDED from the null sup. Returns
   three p-values: SPA_l (lower bound, most conservative, matches RC),
   SPA_c (consistent, recommended), SPA_u (upper bound, optimistic).
   Use SPA_c.

3. **Romano-Wolf stepdown (2005)** — gives PER-STRATEGY adjusted
   p-values controlling FWER under arbitrary dependence. Algorithm:
   sort strategies by test stat descending; for each, p_adj_k =
   P(max over strategies still in play in null ≥ obs_k); strategies
   are pruned once they're rejected. Output: each strategy gets its
   own adjusted p-value, comparable across strategies.

All three tests share the same bootstrap draws (one set of indices,
reused) for consistency.

References:
  White (2000), "A Reality Check for Data Snooping", Econometrica.
  Hansen (2005), "A Test for Superior Predictive Ability", JBES.
  Romano & Wolf (2005), "Stepwise Multiple Testing as Formalized
    Data Snooping", Econometrica.
  Politis & Romano (1994), "The Stationary Bootstrap", JASA.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
def _stationary_block_indices(n: int, block_size: int,
                              rng: np.random.Generator) -> np.ndarray:
    """One stationary-bootstrap (Politis-Romano) index draw of length n.

    Geometric block lengths with mean = block_size; wraps modulo n.
    Same procedure as in harness/bootstrap.py — duplicated here only to
    keep this module dependency-free of the iter-level bootstrap module.
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


def _optimal_block_size(returns: np.ndarray) -> int:
    """Politis-White (2004) plug-in heuristic, simplified.

    For series with little autocorrelation, n^(1/3) is fine.
    For very persistent series, larger blocks would be optimal; the
    cube-root rule is the common default in the empirical-finance
    bootstrap literature and matches what `harness/bootstrap.py` uses.
    """
    n = len(returns)
    return max(2, int(round(n ** (1.0 / 3.0))))


# --------------------------------------------------------------------------- #
def _build_bootstrap_means(
    R: np.ndarray,            # [T, N] mean-recentered returns
    n_boot: int,
    block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Bootstrap distribution of sample means under the H0 mean=0.

    Returns array of shape [n_boot, N]: each row is sqrt(T) · mean(R[idx, :])
    for one stationary-block resample of indices `idx`.

    Crucially we share `idx` across the N columns within each boot
    iteration → preserves the cross-strategy correlation structure.
    """
    T, N = R.shape
    out = np.empty((n_boot, N), dtype=np.float64)
    sqrtT = math.sqrt(T)
    for b in range(n_boot):
        idx = _stationary_block_indices(T, block_size, rng)
        out[b] = sqrtT * R[idx].mean(axis=0)
    return out


# --------------------------------------------------------------------------- #
def reality_check(
    obs_means_sqrtT: np.ndarray,    # [N]
    boot_means_sqrtT: np.ndarray,   # [n_boot, N]
) -> dict[str, Any]:
    """White (2000) Reality Check p-value.

    Test stat = max over k of sqrt(T) · mean_obs_k.
    Null distribution = max over k of boot_k (already centered by construction).
    """
    obs_max = float(np.max(obs_means_sqrtT))
    null_max = boot_means_sqrtT.max(axis=1)
    n_boot = len(null_max)
    p = float((np.sum(null_max >= obs_max) + 1) / (n_boot + 1))
    return {
        "test_stat": obs_max,
        "p_value": p,
        "null_quantiles_05_50_95": [
            float(np.quantile(null_max, q)) for q in (0.05, 0.50, 0.95)
        ],
        "n_boot": int(n_boot),
    }


# --------------------------------------------------------------------------- #
def spa(
    obs_means: np.ndarray,            # [N]  raw observed means (not sqrtT)
    obs_means_sqrtT: np.ndarray,      # [N]
    boot_means_sqrtT: np.ndarray,     # [n_boot, N]
    T: int,
) -> dict[str, Any]:
    """Hansen (2005) SPA test — lower / consistent / upper p-values.

    Studentize by per-strategy bootstrap std (over rows of boot_means_sqrtT).
    Recentering rules differ in how strategies with very negative observed
    means are treated in the null:
      - LOWER : recenter ALL strategies by their observed sqrtT·mean
                (matches RC exactly when no studentization)
      - CONSISTENT : recenter only strategies whose observed t-stat
                exceeds -sqrt(2·log(log(T))). The Hansen-recommended
                default; the threshold goes to -inf slowly, so as T grows
                more strategies are kept, but in finite samples obviously
                bad strategies are excluded from inflating the null sup.
      - UPPER : recenter only strategies with non-negative observed mean.
                Most optimistic; the null sup is taken over only the
                "credible contenders".

    Returns the three p-values plus the studentized observed stat.
    """
    n_boot, N = boot_means_sqrtT.shape

    # Bootstrap std per strategy — used to studentize. Floor to avoid /0
    # for degenerate (all-zero) columns.
    sd = boot_means_sqrtT.std(axis=0, ddof=1)
    sd = np.where(sd > 1e-12, sd, 1e-12)

    # Studentized observed stats and bootstrap stats.
    z_obs = obs_means_sqrtT / sd                          # [N]
    z_boot = boot_means_sqrtT / sd[None, :]               # [n_boot, N]
    # boot_means_sqrtT was already recentered to zero by construction
    # (we passed mean-recentered R into _build_bootstrap_means), so z_boot
    # is the studentized null distribution under "mean=0 across all".

    test_stat = float(z_obs.max())

    # The three Hansen variants differ only in which strategies' boot
    # statistics enter the sup. The "exclude" rules are applied by
    # ZEROING out columns rather than dropping them, so the sup stays
    # well-defined even if all are excluded.
    log_log_T = math.log(max(math.log(max(T, 3)), 1.0))
    threshold = -math.sqrt(2.0 * log_log_T)

    def _pval(mask: np.ndarray) -> float:
        # mask: [N] of True for strategies that contribute to null sup.
        # We zero out the others — they cannot win the sup.
        if not mask.any():
            # Everything excluded → null max is effectively 0 → p_value
            # against positive test_stat is ~smallest possible.
            return 1.0 / (n_boot + 1)
        z_use = np.where(mask[None, :], z_boot, -np.inf)
        null_max = z_use.max(axis=1)
        # Replace any -inf rows (no contributors) with 0 — neutral baseline.
        null_max = np.where(np.isfinite(null_max), null_max, 0.0)
        return float((np.sum(null_max >= test_stat) + 1) / (n_boot + 1))

    mask_lower = np.ones(N, dtype=bool)
    mask_consistent = z_obs >= threshold
    mask_upper = obs_means >= 0

    return {
        "test_stat_studentized": test_stat,
        "p_value_lower": _pval(mask_lower),
        "p_value_consistent": _pval(mask_consistent),
        "p_value_upper": _pval(mask_upper),
        "n_kept_consistent": int(mask_consistent.sum()),
        "n_kept_upper": int(mask_upper.sum()),
        "threshold_consistent": float(threshold),
        "n_boot": int(n_boot),
    }


# --------------------------------------------------------------------------- #
def romano_wolf(
    obs_means_sqrtT: np.ndarray,     # [N]
    boot_means_sqrtT: np.ndarray,    # [n_boot, N]
    names: list[str],
) -> list[dict[str, Any]]:
    """Romano-Wolf (2005) stepdown adjusted p-values, one per strategy.

    Procedure:
      1. Sort strategies by observed stat descending.
      2. For the largest (rank 1): p_adj_1 = P(max over ALL strategies in
         the null ≥ obs_1). If p_adj_1 fails to reject, stop — all
         subsequent strategies inherit p_adj ≥ p_adj_1.
      3. Otherwise drop strategy 1 from consideration. For rank 2:
         p_adj_2 = P(max over REMAINING strategies in the null ≥ obs_2).
         Enforce monotonicity: p_adj_2 := max(p_adj_2, p_adj_1).
      4. Continue.

    The monotonicity step is what gives FWER control under arbitrary
    dependence — see Romano & Wolf §3.

    Returns rows in the SAME order as input `names` (sorted internally
    only for the algorithm). Each row has obs_stat, p_adj, rank, reject_at_05.
    """
    n_boot, N = boot_means_sqrtT.shape

    order = np.argsort(-obs_means_sqrtT)              # descending
    sorted_obs = obs_means_sqrtT[order]
    sorted_names = [names[i] for i in order]

    p_adj_sorted = np.zeros(N)
    alive = np.ones(N, dtype=bool)                    # in sort order
    last_p = 0.0

    for rank in range(N):
        # null sup over strategies still alive (in sorted order)
        cols = order[alive]
        null_max = boot_means_sqrtT[:, cols].max(axis=1)
        p = float((np.sum(null_max >= sorted_obs[rank]) + 1) / (n_boot + 1))
        p = max(p, last_p)                            # monotonicity
        p_adj_sorted[rank] = p
        last_p = p
        # Reject if p < 0.05? Even without rejecting we still pop the
        # currently-highest strategy off the alive set so the next
        # round's null sup is over a smaller family — this is the
        # "stepdown" part. Romano-Wolf rejects at all rank where
        # p_adj < alpha contiguously from the top.
        alive[rank] = False

    # Map back to input order.
    p_adj_by_name = {sorted_names[r]: p_adj_sorted[r] for r in range(N)}
    rank_by_name = {sorted_names[r]: r + 1 for r in range(N)}

    out = []
    for i, name in enumerate(names):
        p = float(p_adj_by_name[name])
        out.append({
            "strategy": name,
            "obs_stat_sqrtT_mean": float(obs_means_sqrtT[i]),
            "rank": int(rank_by_name[name]),
            "p_adj": p,
            "reject_at_05": bool(p < 0.05),
            "reject_at_10": bool(p < 0.10),
        })
    return out


# --------------------------------------------------------------------------- #
def multistrat_tests(
    returns_matrix: pd.DataFrame,
    n_boot: int = 1000,
    block_size: int | None = None,
    seed: int | None = None,
    benchmark: float = 0.0,
) -> dict[str, Any]:
    """Run RC + SPA + Romano-Wolf on a [T × N] matrix of strategy returns.

    Arguments:
      returns_matrix : pd.DataFrame, index = timestamps (any tz),
                       columns = strategy names, values = per-period
                       returns (NOT cumulative). NaNs are dropped jointly
                       (any-NaN row is excluded — preserves alignment).
      benchmark      : H0 is "mean ≤ benchmark". Default 0 = no edge.
                       Pass e.g. risk-free / period to test against rf.
      n_boot         : number of stationary-block bootstrap resamples.
      block_size     : if None, defaults to n^(1/3) (Politis-White rule).
      seed           : RNG seed. None = nondeterministic (uses entropy).

    Returns dict with keys:
      n_strategies, n_periods, block_size, n_boot,
      observed     : per-strategy mean/std/sharpe-proxy,
      reality_check: White (2000) summary,
      spa          : Hansen (2005) summary,
      romano_wolf  : per-strategy adjusted p-values list.
    """
    if returns_matrix.empty or returns_matrix.shape[1] < 1:
        raise ValueError("returns_matrix must be non-empty with ≥1 column")

    R_full = returns_matrix.dropna(how="any")
    if R_full.shape[0] < 30:
        raise ValueError(
            f"need ≥30 jointly-aligned periods, got {R_full.shape[0]} "
            f"(out of {len(returns_matrix)} rows before dropna)"
        )

    names = list(R_full.columns)
    T, N = R_full.shape
    R = R_full.values - benchmark                  # excess over benchmark

    bs = block_size if block_size else _optimal_block_size(R.mean(axis=1))
    rng = np.random.default_rng(seed)

    obs_means = R.mean(axis=0)                     # [N]
    obs_means_sqrtT = math.sqrt(T) * obs_means

    # Mean-recenter for the null. After this each column has mean exactly 0,
    # so the bootstrap means_sqrtT samples are draws from the H0 distribution.
    R_centered = R - obs_means[None, :]
    boot_means_sqrtT = _build_bootstrap_means(R_centered, n_boot, bs, rng)

    rc = reality_check(obs_means_sqrtT, boot_means_sqrtT)
    spa_res = spa(obs_means, obs_means_sqrtT, boot_means_sqrtT, T)
    rw = romano_wolf(obs_means_sqrtT, boot_means_sqrtT, names)

    # Per-strategy descriptive stats (annualization is left to the
    # caller, since cadence varies — runner.multistrat resamples to
    # daily before calling this).
    obs_std = R_full.values.std(axis=0, ddof=1)
    sharpe_proxy = np.where(obs_std > 0, obs_means / obs_std, 0.0)

    per_strategy = []
    rw_by_name = {row["strategy"]: row for row in rw}
    for i, name in enumerate(names):
        per_strategy.append({
            "strategy": name,
            "n_periods": int(T),
            "mean": float(obs_means[i]),
            "std": float(obs_std[i]),
            "sharpe_per_period": float(sharpe_proxy[i]),
            "rw_p_adj": float(rw_by_name[name]["p_adj"]),
            "rw_rank": int(rw_by_name[name]["rank"]),
        })

    return {
        "n_strategies": int(N),
        "n_periods": int(T),
        "block_size": int(bs),
        "n_boot": int(n_boot),
        "benchmark": float(benchmark),
        "period_start": str(R_full.index[0]),
        "period_end": str(R_full.index[-1]),
        "per_strategy": per_strategy,
        "reality_check": rc,
        "spa": spa_res,
        "romano_wolf": rw,
    }
