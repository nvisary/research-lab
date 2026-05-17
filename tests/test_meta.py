"""Tests for harness.meta — MetaLabeler fit/predict + meta_modulate."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from harness.meta import MetaLabeler, MetaSpec, meta_modulate


def _make_dataset(n: int = 600, seed: int = 1):
    """Build a synthetic case where ONE feature is genuinely predictive
    of trade outcome. After fitting, the meta-classifier should learn this.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    # Slow-varying state (AR1) — when positive, NEXT bars drift up.
    f1 = np.zeros(n)
    eps = rng.normal(0.0, 1.0, size=n)
    for i in range(1, n):
        f1[i] = 0.95 * f1[i - 1] + 0.3 * eps[i]
    # Tomorrow's return is driven by TODAY's f1 → causal predictive link.
    log_ret = np.zeros(n)
    noise = rng.normal(0.0, 0.005, size=n)
    log_ret[1:] = 0.012 * np.sign(f1[:-1]) + noise[1:]
    close = 100.0 * np.exp(np.cumsum(log_ret))
    # Primary signal: always long (+1) every 5 bars, so we have many events.
    sig = pd.Series(0.0, index=idx)
    sig.iloc[::5] = 1.0
    vol = pd.Series(0.005, index=idx)
    features = pd.DataFrame({"f1": f1, "noise": rng.normal(0, 1, size=n)},
                             index=idx)
    return sig, pd.Series(close, index=idx, name="close"), features, vol


# --------------------------------------------------------------------------- #
def test_meta_labeler_fits_and_predicts():
    sig, close, feats, vol = _make_dataset(n=600, seed=1)
    spec = MetaSpec(features=["f1", "noise"], classifier="logreg",
                    pt_mult=2.0, sl_mult=1.0, max_holding_bars=10,
                    min_train_events=30)
    labeler = MetaLabeler(spec).fit(sig, close, feats, vol)
    assert labeler.model is not None
    assert labeler.report_ is not None
    assert labeler.report_.n_train_events > 30
    # The predictive feature should dominate importance.
    imp = labeler.report_.feature_importances
    assert imp["f1"] > imp["noise"], imp


def test_meta_labeler_predict_proba_in_unit_interval():
    sig, close, feats, vol = _make_dataset(n=600, seed=2)
    spec = MetaSpec(features=["f1", "noise"], min_train_events=30,
                    max_holding_bars=10)
    labeler = MetaLabeler(spec).fit(sig, close, feats, vol)
    proba = labeler.predict_proba(feats)
    valid = proba.dropna()
    assert ((valid >= 0.0) & (valid <= 1.0)).all()


def test_meta_modulate_scale_multiplies_signal():
    primary = pd.Series([1.0, -1.0, 1.0, 0.0])
    proba = pd.Series([0.8, 0.6, np.nan, 0.7])
    spec = MetaSpec(features=["x"], mode="scale", min_train_events=1)
    out = meta_modulate(primary, proba, spec)
    # 1 * 0.8, -1 * 0.6, 1 * 0.5 (NaN → 0.5), 0 * 0.7
    assert out.tolist() == pytest.approx([0.8, -0.6, 0.5, 0.0])


def test_meta_modulate_gate_zeros_below_threshold():
    primary = pd.Series([1.0, -1.0, 1.0])
    proba = pd.Series([0.8, 0.4, 0.55])
    spec = MetaSpec(features=["x"], mode="gate", threshold=0.5,
                    min_train_events=1)
    out = meta_modulate(primary, proba, spec)
    assert out.tolist() == pytest.approx([1.0, 0.0, 1.0])


def test_meta_labeler_refuses_too_few_events():
    sig, close, feats, vol = _make_dataset(n=50, seed=1)
    # Only ~10 non-zero signal bars → fewer than default 100.
    spec = MetaSpec(features=["f1"], min_train_events=100)
    with pytest.raises(RuntimeError, match="train events"):
        MetaLabeler(spec).fit(sig, close, feats, vol)


def test_meta_labeler_refuses_degenerate_balance():
    """All trades hit PT (or all SL) → no learning signal."""
    n = 300
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    # Monotonically rising price → every long trade hits PT.
    close = pd.Series(100.0 * (1.01 ** np.arange(n)), index=idx, name="close")
    vol = pd.Series(0.005, index=idx)
    sig = pd.Series(0.0, index=idx)
    sig.iloc[::5] = 1.0
    feats = pd.DataFrame({"f1": np.random.randn(n)}, index=idx)
    spec = MetaSpec(features=["f1"], min_train_events=20, max_holding_bars=10,
                    pt_mult=2.0, sl_mult=1.0)
    with pytest.raises(RuntimeError, match="class balance"):
        MetaLabeler(spec).fit(sig, close, feats, vol)
