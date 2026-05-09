"""Tests for capacity / participation metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from harness.backtest import _augment_trades_with_capacity
from harness.metrics import capacity_metrics


# --------------------------------------------------------------------------- #
# capacity_metrics aggregator
# --------------------------------------------------------------------------- #
def test_capacity_metrics_handles_empty():
    out = capacity_metrics(pd.DataFrame())
    assert out["max_participation_pct"] is None
    assert out["n_trades_over_threshold"] == 0


def test_capacity_metrics_handles_missing_column():
    out = capacity_metrics(pd.DataFrame({"entry_time": []}))
    assert out["max_participation_pct"] is None


def test_capacity_metrics_aggregates():
    trades = pd.DataFrame({"participation_pct": [0.5, 1.2, 7.8, np.nan, 12.0]})
    out = capacity_metrics(trades, warn_threshold_pct=5.0)
    assert out["max_participation_pct"] == pytest.approx(12.0)
    assert out["mean_participation_pct"] == pytest.approx((0.5 + 1.2 + 7.8 + 12.0) / 4)
    assert out["n_trades_over_threshold"] == 2  # 7.8 and 12.0
    assert out["pct_trades_over_threshold"] == pytest.approx(50.0)  # 2 of 4 non-NaN


def test_capacity_metrics_threshold_overridable():
    trades = pd.DataFrame({"participation_pct": [1.0, 2.0, 3.0]})
    out = capacity_metrics(trades, warn_threshold_pct=2.5)
    assert out["n_trades_over_threshold"] == 1
    assert out["capacity_threshold_pct"] == 2.5


# --------------------------------------------------------------------------- #
# _augment_trades_with_capacity — joins trades against daily volume
# --------------------------------------------------------------------------- #
def test_augment_computes_participation():
    idx = pd.date_range("2024-01-01", periods=48, freq="1h", tz="UTC")
    prices = pd.DataFrame({"BTCUSDT": 50_000.0}, index=idx)
    # 1 BTC traded per bar × 48 bars = 24 BTC/day × $50k = $1.2M/day
    volumes = pd.DataFrame({"BTCUSDT": 1.0}, index=idx)

    trades = pd.DataFrame({
        "entry_time": [pd.Timestamp("2024-01-01 12:00", tz="UTC")],
        "entry_price": [50_000.0],
        "size": [0.1],         # entry notional = 0.1 * 50k = $5k
        "symbol": ["BTCUSDT"],
    })
    augmented = _augment_trades_with_capacity(trades, prices, volumes)
    assert augmented["entry_notional_usd"].iloc[0] == pytest.approx(5_000.0)
    # Daily $ volume: 24 bars * $50k = $1_200_000 (only Jan 1 has bars 0..23)
    assert augmented["entry_daily_volume_usd"].iloc[0] == pytest.approx(1_200_000.0)
    # Participation: 5000 / 1.2e6 ≈ 0.417%
    assert augmented["participation_pct"].iloc[0] == pytest.approx(5000.0 / 1.2e6 * 100)


def test_augment_handles_unknown_symbol():
    idx = pd.date_range("2024-01-01", periods=24, freq="1h", tz="UTC")
    prices = pd.DataFrame({"BTCUSDT": 50_000.0}, index=idx)
    volumes = pd.DataFrame({"BTCUSDT": 1.0}, index=idx)

    trades = pd.DataFrame({
        "entry_time": [pd.Timestamp("2024-01-01 12:00", tz="UTC")],
        "entry_price": [3.0],
        "size": [100.0],
        "symbol": ["UNKNOWN"],   # not in volumes
    })
    augmented = _augment_trades_with_capacity(trades, prices, volumes)
    assert pd.isna(augmented["entry_daily_volume_usd"].iloc[0])
    assert pd.isna(augmented["participation_pct"].iloc[0])


def test_augment_handles_zero_volume():
    idx = pd.date_range("2024-01-01", periods=24, freq="1h", tz="UTC")
    prices = pd.DataFrame({"BTCUSDT": 50_000.0}, index=idx)
    volumes = pd.DataFrame({"BTCUSDT": 0.0}, index=idx)  # all zero

    trades = pd.DataFrame({
        "entry_time": [pd.Timestamp("2024-01-01 12:00", tz="UTC")],
        "entry_price": [50_000.0],
        "size": [0.1],
        "symbol": ["BTCUSDT"],
    })
    augmented = _augment_trades_with_capacity(trades, prices, volumes)
    # Division by zero → NaN, not Inf.
    assert pd.isna(augmented["participation_pct"].iloc[0])


def test_augment_empty_trades_returns_empty():
    idx = pd.date_range("2024-01-01", periods=24, freq="1h", tz="UTC")
    prices = pd.DataFrame({"BTCUSDT": 50_000.0}, index=idx)
    volumes = pd.DataFrame({"BTCUSDT": 1.0}, index=idx)
    out = _augment_trades_with_capacity(pd.DataFrame(), prices, volumes)
    assert out.empty


# --------------------------------------------------------------------------- #
# End-to-end: summary picks up capacity when trades_in_slice provided
# --------------------------------------------------------------------------- #
def test_summary_includes_capacity_when_trades_passed():
    from harness.metrics import summary
    idx = pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC")
    eq = pd.Series(np.linspace(10_000, 11_000, 200), index=idx)
    rets = eq.pct_change().fillna(0.0)
    pos = pd.DataFrame({"X": np.zeros(200)}, index=idx)
    trades = pd.DataFrame({"participation_pct": [0.1, 0.5, 9.0]})
    out = summary(eq, rets, pos, n_trades=3, tf="1h", trades_in_slice=trades)
    assert out["max_participation_pct"] == pytest.approx(9.0)
    assert out["n_trades_over_threshold"] == 1


def test_summary_capacity_keys_present_when_no_trades():
    from harness.metrics import summary
    idx = pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC")
    eq = pd.Series(np.linspace(10_000, 11_000, 200), index=idx)
    rets = eq.pct_change().fillna(0.0)
    pos = pd.DataFrame({"X": np.zeros(200)}, index=idx)
    out = summary(eq, rets, pos, n_trades=0, tf="1h")
    # Backwards-compat: keys present, values None.
    assert "max_participation_pct" in out
    assert out["max_participation_pct"] is None
    assert out["n_trades_over_threshold"] == 0
