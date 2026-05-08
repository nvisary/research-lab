"""Probabilistic Sharpe Ratio, Deflated Sharpe Ratio, bootstrap CIs.

Defends against two specific biases:

  1. Short / fat-tailed samples: raw Sharpe overstates significance.
     PSR (Bailey & López de Prado, 2012) corrects for sample size, skew,
     and excess kurtosis.

  2. Selection bias: after N tries, the best Sharpe is positive even
     under H0 of zero edge. DSR adjusts PSR for the number of trials.

Both return values in [0, 1] interpretable as "probability the strategy
has true Sharpe > benchmark, given what we observe and how hard we tried".
A best.composite with DSR < 0.5 is essentially noise-fit.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats as sps


def _annualization_factor(idx: pd.DatetimeIndex) -> float:
    if len(idx) < 2:
        return 1.0
    dt_seconds = (idx[-1] - idx[0]).total_seconds() / max(len(idx) - 1, 1)
    return (365.25 * 24 * 3600) / dt_seconds


def _per_period_sharpe(returns: pd.Series) -> tuple[float, float, int]:
    """Return (per-period Sharpe, annualization sqrt-factor, n)."""
    r = returns.dropna()
    if len(r) < 3 or r.std(ddof=0) == 0:
        return 0.0, 1.0, len(r)
    sr_pp = float(r.mean() / r.std(ddof=0))
    ann = math.sqrt(_annualization_factor(r.index))
    return sr_pp, ann, len(r)


def psr(returns: pd.Series, sr_benchmark_annual: float = 0.0) -> float:
    """Probabilistic Sharpe Ratio in [0, 1].

    Bailey & López de Prado (2012). Probability that the true (population) Sharpe
    exceeds `sr_benchmark_annual`, given the observed sample.
    """
    sr_pp, ann, n = _per_period_sharpe(returns)
    if n < 3 or ann == 0:
        return 0.0

    sr_bench_pp = sr_benchmark_annual / ann
    r = returns.dropna()
    skew = float(sps.skew(r, bias=False))
    # excess kurtosis (so a normal distribution gives 0)
    kurt_excess = float(sps.kurtosis(r, fisher=True, bias=False))

    # variance of the Sharpe estimator (per-period units)
    denom = 1.0 - skew * sr_pp + (kurt_excess / 4.0) * sr_pp ** 2
    if denom <= 0:
        return 0.0
    z = (sr_pp - sr_bench_pp) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(sps.norm.cdf(z))


def deflated_sharpe(returns: pd.Series, n_trials: int,
                    trial_sharpes: list[float] | None = None) -> float:
    """Deflated Sharpe Ratio in [0, 1].

    Uses the expected maximum of `n_trials` independent draws under H0:zero edge
    as the benchmark, then runs PSR against that benchmark. If the actual
    distribution of trial Sharpes is available, use its std for a tighter bound.
    Bailey & López de Prado (2014).
    """
    sr_pp, ann, n = _per_period_sharpe(returns)
    if n < 3 or n_trials < 1 or ann == 0:
        return 0.0

    # Expected max of N standard normals (Gumbel approx).
    if n_trials == 1:
        em = 0.0
    else:
        gamma = 0.5772156649  # Euler-Mascheroni
        em = (1 - gamma) * sps.norm.ppf(1 - 1.0 / n_trials) \
             + gamma * sps.norm.ppf(1 - 1.0 / (n_trials * math.e))

    # Std of the trial-Sharpe distribution (per-period units).
    if trial_sharpes and len(trial_sharpes) >= 2:
        sigma_trials_pp = float(np.std([s / ann for s in trial_sharpes], ddof=1))
    else:
        # Fallback: assume unit-variance trial Sharpes (i.e. trials drawn from
        # an unbiased estimator). Conservative.
        sigma_trials_pp = 1.0 / math.sqrt(max(n - 1, 1))

    sr_bench_pp = em * sigma_trials_pp
    sr_bench_annual = sr_bench_pp * ann
    return psr(returns, sr_benchmark_annual=sr_bench_annual)


def bootstrap_sharpe_ci(returns: pd.Series, n_boot: int = 1000,
                        confidence: float = 0.95,
                        block_size: int | None = None,
                        seed: int | None = 42) -> tuple[float, float]:
    """Stationary block bootstrap CI on annualized Sharpe (Politis & Romano).

    Block size defaults to floor(n^(1/3)). With ~5000 hourly returns that's ~17
    bars — sensible for capturing local autocorrelation in 1h crypto data.
    """
    r = returns.dropna().values
    n = len(r)
    if n < 50:
        return (0.0, 0.0)
    if block_size is None:
        block_size = max(2, int(round(n ** (1 / 3))))
    ann = math.sqrt(_annualization_factor(returns.dropna().index))
    rng = np.random.default_rng(seed)

    out = np.empty(n_boot)
    for i in range(n_boot):
        sample = np.empty(n)
        pos = 0
        while pos < n:
            start = rng.integers(0, n)
            length = rng.geometric(1.0 / block_size)
            length = min(length, n - pos)
            for j in range(length):
                sample[pos + j] = r[(start + j) % n]
            pos += length
        std = sample.std(ddof=0)
        out[i] = (sample.mean() / std * ann) if std > 0 else 0.0

    lo = float(np.quantile(out, (1 - confidence) / 2))
    hi = float(np.quantile(out, 1 - (1 - confidence) / 2))
    return lo, hi
