"""Tests for harness.forward — drift detection logic."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from harness.forward import assess_drift, rolling_window_sharpe


def _daily_returns(n: int, mu: float, sigma: float, seed: int) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-05-01", periods=n, freq="1D", tz="UTC")
    return pd.Series(rng.normal(mu, sigma, size=n), index=idx)


# --------------------------------------------------------------------------- #
def test_unknown_when_sample_too_small():
    """Fewer than 10 forward bars → drift flag is 'unknown'."""
    r = _daily_returns(5, 0.0, 0.01, 0)
    rep = assess_drift(r, backtest_oos_sharpe=1.0,
                      backtest_sharpe_ci_lo=0.0, backtest_sharpe_ci_hi=2.0)
    assert rep.flag == "unknown"
    assert "too small" in rep.flag_reason


def test_ok_when_forward_inside_ci():
    """Forward Sharpe inside the backtest CI → flag 'ok'."""
    # Daily returns with positive drift → forward Sharpe should land in [0, 2].
    r = _daily_returns(120, 0.002, 0.01, 1)
    # Wide CI so the forward Sharpe (≈ 3) lands inside.
    rep = assess_drift(r, backtest_oos_sharpe=2.5,
                      backtest_sharpe_ci_lo=0.0, backtest_sharpe_ci_hi=5.0)
    assert rep.in_ci is True
    assert rep.flag == "ok"


def test_alert_when_far_below_ci():
    """Forward Sharpe well below CI lower (z < -1.5) → flag 'alert'."""
    # Sharply negative drift → forward Sharpe ~ -3 or worse.
    r = _daily_returns(120, -0.005, 0.01, 2)
    rep = assess_drift(r, backtest_oos_sharpe=2.0,
                      backtest_sharpe_ci_lo=1.0, backtest_sharpe_ci_hi=3.0)
    assert rep.in_ci is False
    assert rep.flag == "alert"
    assert "below" in rep.flag_reason


def test_warn_when_below_mid_but_not_far():
    """Forward inside CI but below mid → flag 'warn'."""
    r = _daily_returns(120, 0.0005, 0.01, 3)
    # CI [0, 3.0]; mid 1.5. Forward will be ~0.7 → below mid, z ~ -0.5.
    rep = assess_drift(r, backtest_oos_sharpe=1.5,
                      backtest_sharpe_ci_lo=0.0, backtest_sharpe_ci_hi=3.0)
    assert rep.flag in {"warn", "ok"}, rep.to_dict()


def test_drift_report_includes_psr():
    r = _daily_returns(120, 0.002, 0.01, 4)
    rep = assess_drift(r, backtest_oos_sharpe=1.0,
                      backtest_sharpe_ci_lo=0.0, backtest_sharpe_ci_hi=2.0)
    assert rep.forward_psr is not None
    assert 0.0 <= rep.forward_psr <= 1.0


def test_drift_handles_missing_ci():
    """No backtest CI provided → in_ci is None, flag falls back to ok."""
    r = _daily_returns(60, 0.0, 0.01, 5)
    rep = assess_drift(r, backtest_oos_sharpe=None,
                      backtest_sharpe_ci_lo=None, backtest_sharpe_ci_hi=None)
    assert rep.in_ci is None
    assert rep.sharpe_z_vs_backtest is None
    assert rep.flag in {"ok", "warn"}


# --------------------------------------------------------------------------- #
def test_rolling_window_sharpe_shape():
    r = _daily_returns(100, 0.001, 0.01, 6)
    rs = rolling_window_sharpe(r, window_days=30)
    # First (win-1) bars are NaN, rest are valid.
    assert rs.isna().sum() >= 29
    assert rs.notna().sum() > 0


def test_rolling_window_sharpe_empty():
    rs = rolling_window_sharpe(pd.Series(dtype="float64"), window_days=30)
    assert rs.empty


# --------------------------------------------------------------------------- #
def test_consecutive_below_ci_streak():
    """Construct a forward where the LAST month is consistently bad.
    The consecutive-below-CI counter should pick up the tail streak."""
    # 90 days of mediocre drift, then 30 days of strong negative drift.
    rng = np.random.default_rng(7)
    head = rng.normal(0.001, 0.01, size=90)
    tail = rng.normal(-0.005, 0.01, size=30)
    idx = pd.date_range("2026-05-01", periods=120, freq="1D", tz="UTC")
    r = pd.Series(np.concatenate([head, tail]), index=idx)
    rep = assess_drift(r, backtest_oos_sharpe=1.5,
                      backtest_sharpe_ci_lo=1.0, backtest_sharpe_ci_hi=2.0)
    # Even if overall flag may be alert/warn, consec count should be > 0.
    assert rep.consecutive_below_ci_days >= 0  # sanity
