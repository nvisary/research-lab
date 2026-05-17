"""Sanity tests for harness.multistrat — Reality Check / SPA / Romano-Wolf.

The tests use synthetic daily returns: a mix of pure-noise series (H0
true for them) and one or two series with injected positive drift
(H1 true). We verify the joint tests behave as expected under each
configuration.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from harness.multistrat import multistrat_tests


def _gen_series(
    n: int, mu: float, sigma: float, seed: int, name: str,
) -> pd.Series:
    rng = np.random.default_rng(seed)
    r = rng.normal(mu, sigma, size=n)
    idx = pd.date_range("2025-01-01", periods=n, freq="1D", tz="UTC")
    return pd.Series(r, index=idx, name=name)


def _build_matrix(specs: list[tuple[str, float, float, int]],
                  n: int = 400) -> pd.DataFrame:
    series = [_gen_series(n, mu, sigma, seed, name)
              for name, mu, sigma, seed in specs]
    return pd.concat(series, axis=1)


# --------------------------------------------------------------------------- #
def test_all_noise_does_not_reject():
    """With 5 zero-mean noise strategies, neither RC nor SPA should
    reject. p-values should be well above 0.05 (in expectation > 0.5,
    but we just check the conservative bound)."""
    specs = [(f"noise_{i}", 0.0, 0.01, 100 + i) for i in range(5)]
    M = _build_matrix(specs, n=400)
    out = multistrat_tests(M, n_boot=500, seed=42)
    assert out["reality_check"]["p_value"] > 0.05
    assert out["spa"]["p_value_consistent"] > 0.05
    # All Romano-Wolf p_adj should be > 0.05 too (under H0)
    rw = out["romano_wolf"]
    assert all(r["p_adj"] > 0.05 for r in rw)


def test_one_strong_winner_rejects():
    """With 4 noise strategies and 1 with clearly positive mean drift,
    the joint tests should reject H0; Romano-Wolf should pick out the
    winner specifically."""
    specs = [
        ("noise_a", 0.0, 0.01, 1),
        ("noise_b", 0.0, 0.01, 2),
        ("noise_c", 0.0, 0.01, 3),
        ("noise_d", 0.0, 0.01, 4),
        # mu = 0.5% / day, sigma = 1% → strong but not absurd
        ("winner",  0.005, 0.01, 5),
    ]
    M = _build_matrix(specs, n=400)
    out = multistrat_tests(M, n_boot=500, seed=42)
    assert out["reality_check"]["p_value"] < 0.05
    assert out["spa"]["p_value_consistent"] < 0.05

    rw = {r["strategy"]: r for r in out["romano_wolf"]}
    assert rw["winner"]["reject_at_05"], rw["winner"]
    # The pure noise should NOT be flagged (Romano-Wolf gives them
    # large adjusted p-values via monotonicity).
    assert not any(rw[f"noise_{x}"]["reject_at_05"] for x in "abcd")


def test_romano_wolf_monotonic_in_rank():
    """RW p_adj must be non-decreasing in rank — that's the
    monotonicity-enforcement step in the algorithm."""
    specs = [(f"s_{i}", 0.001 * i, 0.01, 10 + i) for i in range(6)]
    M = _build_matrix(specs, n=300)
    out = multistrat_tests(M, n_boot=400, seed=7)
    rw_sorted = sorted(out["romano_wolf"], key=lambda r: r["rank"])
    for a, b in zip(rw_sorted, rw_sorted[1:]):
        assert a["p_adj"] <= b["p_adj"] + 1e-12, (a, b)


def test_spa_bounds_ordering():
    """SPA produces three p-values: lower ≥ consistent ≥ upper.
    Lower is most conservative (≈RC), upper most optimistic."""
    specs = [
        ("good",  0.002, 0.01, 1),
        ("flat",  0.0,   0.01, 2),
        ("bad",  -0.003, 0.01, 3),
    ]
    M = _build_matrix(specs, n=400)
    out = multistrat_tests(M, n_boot=400, seed=11)
    spa = out["spa"]
    assert spa["p_value_lower"] >= spa["p_value_consistent"] - 1e-9
    assert spa["p_value_consistent"] >= spa["p_value_upper"] - 1e-9
    # The "bad" strategy should be excluded from the consistent variant.
    assert spa["n_kept_consistent"] < 3 or spa["n_kept_upper"] < 3


def test_correlated_strategies_handled():
    """Two highly-correlated strategies must not be treated as two
    independent trials. RC/SPA bootstrap preserves joint correlation
    by sharing block indices across columns — so the null max
    distribution is narrower than if they were independent. We just
    sanity-check the procedure runs and gives sensible numbers."""
    n = 400
    rng = np.random.default_rng(123)
    base = rng.normal(0.0, 0.01, size=n)
    idx = pd.date_range("2025-01-01", periods=n, freq="1D", tz="UTC")
    s1 = pd.Series(base + rng.normal(0.0, 0.001, size=n),
                   index=idx, name="s1")
    s2 = pd.Series(base + rng.normal(0.0, 0.001, size=n),
                   index=idx, name="s2")
    # Sanity: these should be very correlated.
    assert s1.corr(s2) > 0.9
    M = pd.concat([s1, s2], axis=1)
    out = multistrat_tests(M, n_boot=400, seed=99)
    # H0 is true; should not reject.
    assert out["reality_check"]["p_value"] > 0.05
    assert out["spa"]["p_value_consistent"] > 0.05


def test_too_short_raises():
    """multistrat_tests refuses fewer than 30 jointly-aligned obs."""
    specs = [(f"s_{i}", 0.0, 0.01, i) for i in range(3)]
    M = _build_matrix(specs, n=20)
    with pytest.raises(ValueError):
        multistrat_tests(M, n_boot=200, seed=1)


def test_p_values_in_unit_interval():
    """All reported p-values must lie in (0, 1]."""
    specs = [(f"s_{i}", 0.0, 0.01, i) for i in range(4)]
    M = _build_matrix(specs, n=200)
    out = multistrat_tests(M, n_boot=300, seed=2)
    assert 0.0 < out["reality_check"]["p_value"] <= 1.0
    for k in ("p_value_lower", "p_value_consistent", "p_value_upper"):
        assert 0.0 < out["spa"][k] <= 1.0, (k, out["spa"][k])
    for r in out["romano_wolf"]:
        assert 0.0 < r["p_adj"] <= 1.0, r
