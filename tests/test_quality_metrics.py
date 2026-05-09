"""Tests for quality_metrics: problem-detection diagnostics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from harness.metrics import (
    _longest_true_run,
    aggregate_wf_composite,
    quality_metrics,
    summary,
)


# --------------------------------------------------------------------------- #
# _longest_true_run
# --------------------------------------------------------------------------- #
def test_longest_true_run_empty():
    assert _longest_true_run(np.array([], dtype=bool)) == 0


def test_longest_true_run_all_false():
    assert _longest_true_run(np.array([False, False, False])) == 0


def test_longest_true_run_basic():
    # T T F T T T F T → 3
    arr = np.array([True, True, False, True, True, True, False, True])
    assert _longest_true_run(arr) == 3


def test_longest_true_run_all_true():
    assert _longest_true_run(np.array([True] * 10)) == 10


# --------------------------------------------------------------------------- #
# pct_positive_months
# --------------------------------------------------------------------------- #
def test_pct_positive_months():
    # Build 12 months of monthly equity with 8 positive moves and 4 negative.
    idx = pd.date_range("2024-01-01", periods=13, freq="MS", tz="UTC")
    # equity sequence: starts at 100. +8 ups of +1, then 4 downs of -1.
    moves = [+1.0] * 8 + [-1.0] * 4
    equity_vals = np.concatenate([[100.0], 100.0 + np.cumsum(moves)])
    eq = pd.Series(equity_vals, index=idx)
    out = quality_metrics(eq, eq.pct_change().fillna(0), None, None, tf="1d")
    # 12 monthly returns total, 8 positive → 66.67%
    assert out["pct_positive_months"] == pytest.approx(8 / 12 * 100.0, rel=1e-3)


# --------------------------------------------------------------------------- #
# longest_underwater
# --------------------------------------------------------------------------- #
def test_longest_underwater_bars_simple():
    # equity goes up 5 bars, then down 7 bars, then up 1.
    # cummax stays at peak; bars 6..12 are underwater (7 bars).
    eq_vals = [100, 101, 102, 103, 104, 105, 104, 103, 102, 101, 100, 99, 98, 99]
    idx = pd.date_range("2024-01-01", periods=len(eq_vals), freq="1h", tz="UTC")
    eq = pd.Series(eq_vals, dtype=float, index=idx)
    out = quality_metrics(eq, eq.pct_change().fillna(0), None, None, tf="1h")
    assert out["longest_underwater_bars"] == 8  # bars from peak to recovery
    # 8 bars at 1h = 8/24 days
    assert out["longest_underwater_days"] == pytest.approx(8 / 24, rel=1e-3)


# --------------------------------------------------------------------------- #
# pain_index (Ulcer)
# --------------------------------------------------------------------------- #
def test_pain_index_zero_when_monotonic():
    eq = pd.Series([100.0, 101.0, 102.0, 103.0],
                   index=pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC"))
    out = quality_metrics(eq, eq.pct_change().fillna(0), None, None, tf="1h")
    assert out["pain_index"] == pytest.approx(0.0, abs=1e-9)


def test_pain_index_positive_when_drawdown():
    eq = pd.Series([100.0, 110.0, 90.0, 100.0],
                   index=pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC"))
    out = quality_metrics(eq, eq.pct_change().fillna(0), None, None, tf="1h")
    # DD path: 0%, 0%, -18.18%, -9.09%
    # pain = sqrt(mean([0, 0, 0.1818^2, 0.0909^2])) ≈ 0.1016
    assert out["pain_index"] == pytest.approx(0.1016, abs=0.005)


# --------------------------------------------------------------------------- #
# pnl_concentration
# --------------------------------------------------------------------------- #
def test_pnl_concentration_top1():
    trades = pd.DataFrame({
        "pnl_quote": [100.0, 50.0, 30.0, 20.0, -50.0, -30.0],
    })
    # total = 120, top1 positive = 100. Concentration = 100/120 = 83.3%.
    out = quality_metrics(pd.Series([], dtype=float), pd.Series([], dtype=float),
                          None, trades, tf="1h")
    assert out["pnl_concentration_top1_pct"] == pytest.approx(100 / 120 * 100, abs=0.5)


def test_pnl_concentration_top5_caps_at_available():
    trades = pd.DataFrame({"pnl_quote": [10.0, 5.0, -2.0]})
    # Only 2 positive trades; top5 falls back to all positives (15 / 13 = 115%)
    out = quality_metrics(pd.Series([], dtype=float), pd.Series([], dtype=float),
                          None, trades, tf="1h")
    # total = 13, sum positives = 15. Concentration = 15/13 ≈ 115%.
    assert out["pnl_concentration_top5_pct"] == pytest.approx(15 / 13 * 100, rel=1e-3)


# --------------------------------------------------------------------------- #
# tail_ratio
# --------------------------------------------------------------------------- #
def test_tail_ratio_symmetric():
    # Symmetric returns → tail_ratio ≈ 1.0
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0, 0.01, 1000))
    out = quality_metrics(pd.Series([], dtype=float), r, None, None, tf="1h")
    assert 0.85 < out["tail_ratio"] < 1.15


def test_tail_ratio_asymmetric_negative():
    # Negative-skewed returns: small wins, occasional big losses → tail_ratio < 1
    rng = np.random.default_rng(0)
    r_pos = rng.normal(0.01, 0.005, 900)
    r_neg = rng.normal(-0.05, 0.02, 100)
    r = pd.Series(np.concatenate([r_pos, r_neg]))
    out = quality_metrics(pd.Series([], dtype=float), r, None, None, tf="1h")
    assert out["tail_ratio"] < 0.7


# --------------------------------------------------------------------------- #
# pct_time_in_position
# --------------------------------------------------------------------------- #
def test_pct_time_in_position():
    idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    pos = pd.DataFrame({"BTC": [0, 0, 1, 1, 1, 0, 0, -1, -1, 0]}, index=idx)
    out = quality_metrics(pd.Series([], dtype=float), pd.Series([], dtype=float),
                          pos, None, tf="1h")
    # 5 of 10 bars have non-zero pos → 50%
    assert out["pct_time_in_position"] == 50.0


# --------------------------------------------------------------------------- #
# Trade durations
# --------------------------------------------------------------------------- #
def test_trade_durations():
    trades = pd.DataFrame({"duration_hours": [1.0, 5.0, 10.0, 100.0]})
    out = quality_metrics(pd.Series([], dtype=float), pd.Series([], dtype=float),
                          None, trades, tf="1h")
    assert out["avg_trade_duration_hours"] == pytest.approx(29.0)
    assert out["median_trade_duration_hours"] == pytest.approx(7.5)


# --------------------------------------------------------------------------- #
# Empty inputs degrade gracefully
# --------------------------------------------------------------------------- #
def test_quality_metrics_empty_inputs():
    out = quality_metrics(pd.Series([], dtype=float), pd.Series([], dtype=float),
                          None, None, tf="1h")
    # All None values, no exceptions.
    for k in ("pct_positive_months", "longest_underwater_bars",
              "pnl_concentration_top5_pct", "tail_ratio",
              "pain_index", "pct_time_in_position", "avg_trade_duration_hours",
              "skew", "kurt"):
        assert out[k] is None, f"{k} should be None for empty input"


# --------------------------------------------------------------------------- #
# summary() integration: quality keys present
# --------------------------------------------------------------------------- #
def test_summary_includes_quality_keys():
    idx = pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC")
    eq = pd.Series(np.linspace(10_000, 11_000, 200), index=idx)
    rets = eq.pct_change().fillna(0.0)
    pos = pd.DataFrame({"X": np.tile([0, 1], 100)}, index=idx)
    s = summary(eq, rets, pos, n_trades=10, tf="1h")
    expected_keys = {
        "pct_positive_months", "longest_underwater_bars", "longest_underwater_days",
        "pnl_concentration_top5_pct", "pnl_concentration_top1_pct",
        "tail_ratio", "pain_index", "pct_time_in_position",
        "avg_trade_duration_hours", "median_trade_duration_hours",
        "skew", "kurt",
    }
    assert expected_keys.issubset(s.keys())


# --------------------------------------------------------------------------- #
# WF aggregate quality keys
# --------------------------------------------------------------------------- #
def test_wf_aggregate_quality_metrics():
    fake_windows = [
        {"sharpe": 1.0, "max_dd": 0.05, "n_trades": 60,
         "pct_positive_months": 60.0, "longest_underwater_bars": 100,
         "longest_underwater_days": 4.16, "pnl_concentration_top5_pct": 50.0,
         "pnl_concentration_top1_pct": 30.0, "tail_ratio": 1.1,
         "pain_index": 0.02, "pct_time_in_position": 70.0,
         "avg_trade_duration_hours": 24.0, "skew": -0.1, "kurt": 1.0,
         "sharpe_gap": 0.5},
        {"sharpe": 2.0, "max_dd": 0.10, "n_trades": 80,
         "pct_positive_months": 75.0, "longest_underwater_bars": 200,
         "longest_underwater_days": 8.33, "pnl_concentration_top5_pct": 80.0,
         "pnl_concentration_top1_pct": 60.0, "tail_ratio": 0.9,
         "pain_index": 0.04, "pct_time_in_position": 60.0,
         "avg_trade_duration_hours": 30.0, "skew": 0.2, "kurt": 1.5,
         "sharpe_gap": 1.5},
    ]
    score, agg = aggregate_wf_composite(fake_windows)
    assert score != float("-inf")
    # Worst-case for pain indicators
    assert agg["worst_longest_underwater_bars"] == 200
    assert agg["worst_pnl_concentration_top5_pct"] == 80.0
    assert agg["worst_pain_index"] == pytest.approx(0.04)
    assert agg["worst_sharpe_gap"] == pytest.approx(1.5)
    # Means
    assert agg["mean_pct_positive_months"] == pytest.approx(67.5)
    assert agg["mean_pct_time_in_position"] == pytest.approx(65.0)
    assert agg["mean_sharpe_gap"] == pytest.approx(1.0)
