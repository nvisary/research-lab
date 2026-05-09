"""Tests for Roll-estimator spread estimation.

Strategy: build synthetic series with KNOWN microstructure properties
(bid-ask bounce of fixed width, or pure random walk with no bounce) and
verify the estimator recovers the truth (or correctly falls back).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from datafeed.spreads import (
    SPREAD_FLOOR_BPS,
    estimate_spread_series,
    hl_proxy_spread_bps,
    roll_spread_bps,
    summarize,
)


def _make_bid_ask_bounce_series(n: int, mid: float, half_spread_bps: float,
                                 seed: int = 0) -> pd.Series:
    """Synthetic series with constant mid, alternating bid/ask trades.

    Each trade is mid * (1 ± half_spread_bps/1e4), sign chosen randomly.
    Roll's estimator on this should recover ~ 2*half_spread_bps (the full
    spread) — that's the textbook test.
    """
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1, 1], size=n)
    half_spread = mid * half_spread_bps * 1e-4
    prices = mid + signs * half_spread
    idx = pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC")
    return pd.Series(prices, index=idx, name="close")


def _make_trending_series(n: int, mid: float, phi: float = 0.4,
                          noise_bps: float = 2.0, seed: int = 0) -> pd.Series:
    """AR(1) returns with positive phi — momentum, no bid-ask bounce.

    Lag-1 autocovariance is dominated by phi * Var(r) > 0, so Roll's
    estimator (which expects negative covariance from bouncing) must
    fall back. Used to test the fallback path.
    """
    rng = np.random.default_rng(seed)
    sigma = noise_bps * 1e-4
    r = np.zeros(n)
    for t in range(1, n):
        r[t] = phi * r[t - 1] + rng.normal(0.0, sigma)
    prices = mid * np.exp(np.cumsum(r))
    idx = pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC")
    return pd.Series(prices, index=idx, name="close")


# --------------------------------------------------------------------------- #
# roll_spread_bps
# --------------------------------------------------------------------------- #
def test_roll_recovers_known_spread_on_pure_bounce():
    # 5 bps half-spread → 10 bps full spread expected.
    s = _make_bid_ask_bounce_series(n=5000, mid=50_000.0, half_spread_bps=5.0, seed=42)
    spread, fallback = roll_spread_bps(s)
    assert not fallback
    # Tolerance: ±20% — Roll is noisy on finite samples but the central
    # estimate should be unmistakably near 10 bps and clearly NOT 1 or 100.
    assert 8.0 < spread < 12.0, f"expected ~10 bps, got {spread}"


def test_roll_falls_back_on_trending_series():
    s = _make_trending_series(n=5000, mid=50_000.0)
    spread, fallback = roll_spread_bps(s)
    assert fallback, "trending series has positive serial cov; must fallback"


def test_roll_falls_back_on_too_few_bars():
    s = _make_bid_ask_bounce_series(n=10, mid=50_000.0, half_spread_bps=5.0)
    spread, fallback = roll_spread_bps(s)
    assert fallback
    assert spread == SPREAD_FLOOR_BPS


def test_roll_floors_at_spread_floor():
    # Tiny half-spread — pure noise might still give a Roll < FLOOR. Floor wins.
    s = _make_bid_ask_bounce_series(n=5000, mid=50_000.0, half_spread_bps=0.05, seed=1)
    spread, fallback = roll_spread_bps(s)
    if not fallback:
        assert spread >= SPREAD_FLOOR_BPS


# --------------------------------------------------------------------------- #
# hl_proxy_spread_bps
# --------------------------------------------------------------------------- #
def test_hl_proxy_uses_high_low_range():
    df = pd.DataFrame({
        "high": [100.5, 100.6, 100.4],
        "low":  [100.0, 100.1, 100.0],
        "close":[100.2, 100.4, 100.2],
    })
    proxy = hl_proxy_spread_bps(df)
    # Average range ≈ 0.5 / 100.27 ≈ 50 bps; × 0.5 multiplier = 25 bps.
    assert 20.0 < proxy < 30.0


def test_hl_proxy_floors():
    # Constant prices — range = 0 → proxy goes below floor → returns floor.
    df = pd.DataFrame({"high": [100, 100], "low": [100, 100], "close": [100, 100]})
    assert hl_proxy_spread_bps(df) == SPREAD_FLOOR_BPS


def test_hl_proxy_handles_empty():
    assert hl_proxy_spread_bps(pd.DataFrame()) == SPREAD_FLOOR_BPS


# --------------------------------------------------------------------------- #
# estimate_spread_series — bucketed pipeline
# --------------------------------------------------------------------------- #
def test_estimate_spread_series_buckets_correctly():
    # 3 hours × 60 bars = 180 bars, 1H bucketing → 3 rows.
    s = _make_bid_ask_bounce_series(n=180, mid=50_000.0, half_spread_bps=5.0, seed=7)
    df = pd.DataFrame({"close": s, "high": s * 1.0001, "low": s * 0.9999})
    result = estimate_spread_series(df, bucket="1h")
    assert len(result) == 3
    assert set(result.columns) == {"bucket_start", "spread_bps", "n_bars", "fallback_used"}
    # Each bucket should hit Roll path successfully (60 bars > MIN_BARS_FOR_ROLL=30).
    assert (~result["fallback_used"]).all()
    # Estimates should cluster around 10 bps.
    assert result["spread_bps"].between(5, 20).all()


def test_estimate_spread_series_uses_hl_fallback_when_roll_undefined():
    # Trending series — Roll fails, fallback to HL proxy.
    s = _make_trending_series(n=120, mid=50_000.0)
    df = pd.DataFrame({"close": s, "high": s * 1.001, "low": s * 0.999})
    result = estimate_spread_series(df, bucket="1h")
    assert len(result) == 2
    assert result["fallback_used"].all()
    # HL range is ~20 bps × 0.5 = 10 bps proxy.
    assert result["spread_bps"].between(5, 20).all()


def test_estimate_spread_series_handles_empty():
    df = pd.DataFrame(columns=["close", "high", "low"])
    result = estimate_spread_series(df, bucket="1h")
    assert result.empty


def test_estimate_spread_series_no_hl_columns_uses_floor():
    # Trending close-only series → Roll fails, no HL → floor.
    s = _make_trending_series(n=120, mid=50_000.0)
    df = pd.DataFrame({"close": s})
    result = estimate_spread_series(df, bucket="1h")
    assert (result["spread_bps"] == SPREAD_FLOOR_BPS).all()
    assert result["fallback_used"].all()


# --------------------------------------------------------------------------- #
# summarize
# --------------------------------------------------------------------------- #
def test_summarize_aggregates():
    df = pd.DataFrame({
        "spread_bps": [1.0, 2.0, 3.0, 4.0, 100.0],
        "fallback_used": [False, False, False, True, True],
        "bucket_start": pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC"),
        "n_bars": [60, 60, 60, 60, 60],
    })
    s = summarize(df)
    assert s["n_buckets"] == 5
    assert s["mean_bps"] == 22.0
    assert s["median_bps"] == 3.0
    assert s["fallback_pct"] == 40.0


def test_summarize_handles_empty():
    s = summarize(pd.DataFrame(columns=["spread_bps", "fallback_used"]))
    assert s["n_buckets"] == 0
    assert np.isnan(s["mean_bps"])
