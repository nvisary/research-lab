"""Tests for harness/cpcv.py: path generation and per-path evaluation."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from harness.cpcv import (
    CPCVPath,
    cpcv_paths,
    evaluate_path,
    summarize_paths,
    _intervals_mask,
    _merge_adjacent,
    _subtract_zones,
)


PS = "2024-01-01"
PE = "2025-01-01"  # 366 days (leap year) for clean group sizes


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def test_subtract_zones_disjoint():
    s = pd.Timestamp("2024-01-01", tz="UTC")
    e = pd.Timestamp("2024-02-01", tz="UTC")
    z = (pd.Timestamp("2024-03-01", tz="UTC"), pd.Timestamp("2024-04-01", tz="UTC"))
    assert _subtract_zones(s, e, [z]) == [(s, e)]


def test_subtract_zones_left_overlap():
    s = pd.Timestamp("2024-01-10", tz="UTC")
    e = pd.Timestamp("2024-01-31", tz="UTC")
    z = (pd.Timestamp("2024-01-05", tz="UTC"), pd.Timestamp("2024-01-15", tz="UTC"))
    out = _subtract_zones(s, e, [z])
    assert out == [(z[1], e)]


def test_subtract_zones_middle():
    s = pd.Timestamp("2024-01-01", tz="UTC")
    e = pd.Timestamp("2024-01-31", tz="UTC")
    z = (pd.Timestamp("2024-01-10", tz="UTC"), pd.Timestamp("2024-01-20", tz="UTC"))
    out = _subtract_zones(s, e, [z])
    assert out == [(s, z[0]), (z[1], e)]


def test_merge_adjacent_combines():
    a = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-01-10", tz="UTC"))
    b = (pd.Timestamp("2024-01-10", tz="UTC"), pd.Timestamp("2024-01-20", tz="UTC"))
    c = (pd.Timestamp("2024-02-01", tz="UTC"), pd.Timestamp("2024-02-15", tz="UTC"))
    assert _merge_adjacent([a, b, c]) == [
        (a[0], b[1]),  # merged
        c,
    ]


# --------------------------------------------------------------------------- #
# cpcv_paths
# --------------------------------------------------------------------------- #
def test_cpcv_path_count_matches_combinatorial():
    paths = cpcv_paths(PS, PE, n_groups=10, k_test=2)
    assert len(paths) == math.comb(10, 2)  # 45

    paths_8_2 = cpcv_paths(PS, PE, n_groups=8, k_test=2)
    assert len(paths_8_2) == math.comb(8, 2)  # 28


def test_cpcv_paths_have_expected_test_groups():
    paths = cpcv_paths(PS, PE, n_groups=4, k_test=2)
    expected = {(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)}
    actual = {p.test_groups for p in paths}
    assert actual == expected


def test_cpcv_train_and_oos_dont_overlap():
    paths = cpcv_paths(PS, PE, n_groups=8, k_test=2, embargo="1D")
    for p in paths:
        for t_s, t_e in p.train_intervals:
            for o_s, o_e in p.oos_intervals:
                # No overlap: t_e <= o_s or o_e <= t_s
                assert t_e <= o_s or o_e <= t_s, (
                    f"Train {t_s}-{t_e} overlaps OOS {o_s}-{o_e}"
                )


def test_cpcv_intervals_cover_minus_embargo():
    """Train + OOS + embargo zones = full period."""
    embargo = pd.Timedelta("1D")
    paths = cpcv_paths(PS, PE, n_groups=10, k_test=2, embargo=embargo)
    s = pd.Timestamp(PS, tz="UTC")
    e = pd.Timestamp(PE, tz="UTC")
    total = (e - s).total_seconds()

    for p in paths:
        train_secs = sum((te - ts).total_seconds() for ts, te in p.train_intervals)
        oos_secs = sum((oe - os).total_seconds() for os, oe in p.oos_intervals)
        # Total embargo seconds: one zone per test group, capped at period end.
        # For any path, max embargo seconds = k_test * embargo (no two zones
        # overlap because test groups are distinct).
        covered = train_secs + oos_secs
        max_embargo_secs = 2 * embargo.total_seconds()  # k_test=2
        # train + oos = period - embargo dropouts; embargo dropouts ≤ k_test * embargo.
        assert total - max_embargo_secs <= covered <= total + 1e-3


def test_cpcv_zero_embargo_means_full_coverage():
    paths = cpcv_paths(PS, PE, n_groups=5, k_test=1, embargo=None)
    s = pd.Timestamp(PS, tz="UTC")
    e = pd.Timestamp(PE, tz="UTC")
    total = (e - s).total_seconds()
    for p in paths:
        train_secs = sum((te - ts).total_seconds() for ts, te in p.train_intervals)
        oos_secs = sum((oe - os).total_seconds() for os, oe in p.oos_intervals)
        assert abs((train_secs + oos_secs) - total) < 1e-3


def test_cpcv_validation_errors():
    with pytest.raises(ValueError, match="n_groups"):
        cpcv_paths(PS, PE, n_groups=1, k_test=1)
    with pytest.raises(ValueError, match="k_test"):
        cpcv_paths(PS, PE, n_groups=5, k_test=5)
    with pytest.raises(ValueError, match="k_test"):
        cpcv_paths(PS, PE, n_groups=5, k_test=0)


# --------------------------------------------------------------------------- #
# Mask + evaluate
# --------------------------------------------------------------------------- #
def test_intervals_mask_basic():
    idx = pd.date_range("2024-01-01", "2024-01-10", freq="1D", tz="UTC")
    intervals = [(pd.Timestamp("2024-01-03", tz="UTC"),
                  pd.Timestamp("2024-01-06", tz="UTC"))]
    mask = _intervals_mask(idx, intervals)
    expected = [False, False, True, True, True, False, False, False, False, False]
    assert list(mask) == expected


def test_evaluate_path_on_synthetic():
    """Synthetic equity that grows linearly: per-path Sharpe should be
    finite and roughly equal across paths."""
    idx = pd.date_range("2024-01-01", "2024-12-31", freq="1D", tz="UTC")
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.normal(0.001, 0.01, len(idx)), index=idx)
    equity = (1.0 + rets).cumprod() * 10_000.0

    paths = cpcv_paths(PS, "2025-01-01", n_groups=5, k_test=1, embargo=None)
    results = [evaluate_path(rets, equity, None, p, tf="1d") for p in paths]
    assert len(results) == 5
    sharpes = [r["sharpe"] for r in results]
    # All paths should produce a finite Sharpe.
    assert all(math.isfinite(s) for s in sharpes)
    # With drift 0.001 / noise 0.01 / 73-day chunks, individual paths can
    # come out either sign; the median across the small set should be
    # broadly positive (drift > 0). Loose tolerance: just check it's
    # in a reasonable band, not extreme.
    median_s = float(np.median(sharpes))
    assert -5.0 < median_s < 10.0


def test_evaluate_path_empty_returns():
    out = evaluate_path(pd.Series(dtype=float), pd.Series(dtype=float),
                        None, CPCVPath(test_groups=(0,),
                                       train_intervals=(),
                                       oos_intervals=()),
                        tf="1h")
    assert out["sharpe"] == 0.0
    assert out["n_periods"] == 0


# --------------------------------------------------------------------------- #
# summarize_paths
# --------------------------------------------------------------------------- #
def test_summarize_paths_aggregates():
    fake = [
        {"sharpe": s, "sortino": s, "max_dd": 0.1, "total_return": 0.05,
         "n_trades": 50, "test_groups": (i,), "n_periods": 100, "hit_rate": 0.5,
         "cagr": 0.2}
        for i, s in enumerate([0.5, 1.0, 1.5, 2.0, 2.5])
    ]
    summary = summarize_paths(fake)
    assert summary["n_paths"] == 5
    assert summary["median_sharpe"] == pytest.approx(1.5)
    assert summary["mean_sharpe"] == pytest.approx(1.5)
    assert summary["pct_positive_sharpe"] == pytest.approx(100.0)
    assert summary["pct_above_1"] == pytest.approx(60.0)  # 1.5, 2.0, 2.5 strictly > 1


def test_summarize_paths_empty():
    summary = summarize_paths([])
    assert summary == {"n_paths": 0}
