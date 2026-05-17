"""Tests for triple-barrier labeling and fractional differentiation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from harness.labels import (
    TripleBarrierConfig,
    frac_diff,
    meta_labels,
    triple_barrier_labels,
)


def _synthetic_path(n: int = 100, seed: int = 0,
                    drift: float = 0.0, sigma: float = 0.01) -> pd.Series:
    rng = np.random.default_rng(seed)
    log_ret = rng.normal(drift, sigma, size=n)
    price = 100.0 * np.exp(np.cumsum(log_ret))
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.Series(price, index=idx, name="close")


# --------------------------------------------------------------------------- #
def test_triple_barrier_pt_hit():
    """Construct a path that monotonically rises by 5% in 5 bars → PT should hit."""
    n = 50
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    # Constant +1% per bar growth.
    price = pd.Series(100.0 * (1.01 ** np.arange(n)), index=idx, name="close")
    vol = pd.Series(0.01, index=idx)  # 1% per-bar vol → PT at +2%, SL at -1%
    events = pd.DataFrame({"side": [1]}, index=[idx[0]])
    cfg = TripleBarrierConfig(pt_mult=2.0, sl_mult=1.0, max_holding_bars=20)
    tb = triple_barrier_labels(price, events, vol, cfg)
    assert (tb["bin"] == 1).all(), tb
    # PT crossed somewhere in the first few bars (1% per bar, PT = 2%).
    assert tb.iloc[0]["t1"] < idx[5]


def test_triple_barrier_sl_hit():
    """Monotonic -1.5%/bar → SL should hit before max_holding."""
    n = 30
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    price = pd.Series(100.0 * (0.985 ** np.arange(n)), index=idx, name="close")
    vol = pd.Series(0.01, index=idx)
    events = pd.DataFrame({"side": [1]}, index=[idx[0]])
    cfg = TripleBarrierConfig(pt_mult=2.0, sl_mult=1.0, max_holding_bars=20)
    tb = triple_barrier_labels(price, events, vol, cfg)
    assert (tb["bin"] == -1).all()


def test_triple_barrier_vertical_hit():
    """Flat price → never hits PT/SL → vertical barrier (bin=0)."""
    n = 30
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    price = pd.Series(100.0, index=idx, name="close")
    vol = pd.Series(0.01, index=idx)
    events = pd.DataFrame({"side": [1]}, index=[idx[0]])
    cfg = TripleBarrierConfig(pt_mult=2.0, sl_mult=1.0, max_holding_bars=5)
    tb = triple_barrier_labels(price, events, vol, cfg)
    assert (tb["bin"] == 0).all()
    assert tb.iloc[0]["t1"] == idx[5]


def test_triple_barrier_short_side():
    """For short side (-1), a PRICE DROP is the PT."""
    n = 30
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    price = pd.Series(100.0 * (0.985 ** np.arange(n)), index=idx, name="close")
    vol = pd.Series(0.01, index=idx)
    events = pd.DataFrame({"side": [-1]}, index=[idx[0]])
    cfg = TripleBarrierConfig(pt_mult=2.0, sl_mult=1.0, max_holding_bars=20)
    tb = triple_barrier_labels(price, events, vol, cfg)
    # For short, falling price = profit = PT hit.
    assert (tb["bin"] == 1).all(), tb
    assert (tb["ret"] > 0).all()


def test_meta_labels_basic():
    """meta_labels turns a non-zero primary signal into a y=0/1 dataframe."""
    n = 100
    price = _synthetic_path(n=n, seed=42, drift=0.001, sigma=0.005)
    vol = pd.Series(0.005, index=price.index)
    sig = pd.Series(0.0, index=price.index)
    sig.iloc[[5, 25, 50, 75]] = 1.0
    out = meta_labels(sig, price, vol, pt_mult=2.0, sl_mult=1.0,
                      max_holding_bars=15)
    assert len(out) == 4
    assert set(out["y"].unique()).issubset({0, 1})


def test_meta_labels_empty_primary():
    """All-zero primary signal → empty meta-labels frame."""
    price = _synthetic_path()
    vol = pd.Series(0.01, index=price.index)
    sig = pd.Series(0.0, index=price.index)
    out = meta_labels(sig, price, vol, max_holding_bars=10)
    assert out.empty


# --------------------------------------------------------------------------- #
def test_frac_diff_stationarises_unit_root():
    """A random walk is non-stationary (d=1 fully differences it to white noise).
    Fractional diff with d=0.4 should retain a lot of long memory but the
    output series should have much smaller drift than the original."""
    rng = np.random.default_rng(7)
    n = 1500
    log_ret = rng.normal(0.0, 0.01, size=n)
    price = pd.Series(100.0 * np.exp(np.cumsum(log_ret)),
                      index=pd.date_range("2025-01-01", periods=n,
                                           freq="1h", tz="UTC"))
    fd = frac_diff(price, d=0.4)
    fd_clean = fd.dropna()
    # The fractional-diff series should have a far smaller absolute mean
    # than the original (which has linear drift in log-price).
    assert abs(fd_clean.mean()) < abs(price.mean())
    # And no NaN remaining past the warmup window.
    assert len(fd_clean) > 1000


def test_frac_diff_d_zero_is_identity():
    """d=0 → return series unchanged."""
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    out = frac_diff(s, d=0.0)
    pd.testing.assert_series_equal(out, s)


def test_frac_diff_d_one_is_first_diff():
    """d=1 → first differences."""
    s = pd.Series([1.0, 3.0, 6.0, 10.0])
    out = frac_diff(s, d=1.0)
    pd.testing.assert_series_equal(out, s.diff())


def test_frac_diff_invalid_d():
    """d outside [0, 1] is rejected."""
    s = pd.Series([1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        frac_diff(s, d=1.5)
    with pytest.raises(ValueError):
        frac_diff(s, d=-0.1)
