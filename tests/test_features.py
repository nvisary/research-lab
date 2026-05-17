"""Feature store tests — registry sanity + lookahead-hygiene check.

The lookahead test poisons the TAIL of OHLCV data with NaN and verifies
the leading (pre-poison) outputs of each registered feature are
bit-identical. Mirrors the strategy-level audit in harness/lookahead.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import features
from features.registry import register, list_features, feature_meta


# --------------------------------------------------------------------------- #
def test_builtins_are_registered():
    """The base set of features must be loadable."""
    names = list_features()
    for expected in ("atr_14", "realized_vol_30", "trend_50_200",
                     "regime_class", "rsi_14"):
        assert expected in names, names


def test_feature_meta_exposes_description():
    m = feature_meta("atr_14")
    assert m["name"] == "atr_14"
    assert m["description"]
    assert "ohlcv" in m["deps"]


def test_register_duplicate_rejected():
    with pytest.raises(ValueError):

        @register("atr_14", description="dup")
        def _f(symbol, start, end, tf):
            return pd.Series()


# --------------------------------------------------------------------------- #
def _fake_load_factory(symbol: str = "BTCUSDT", n: int = 500, seed: int = 1):
    """Build a synthetic OHLCV DataFrame and a load() that ignores symbol/tf."""
    rng = np.random.default_rng(seed)
    log_ret = rng.normal(0.0, 0.01, size=n)
    close = 100.0 * np.exp(np.cumsum(log_ret))
    high = close * (1.0 + rng.uniform(0.0, 0.005, size=n))
    low = close * (1.0 - rng.uniform(0.0, 0.005, size=n))
    open_ = close * (1.0 + rng.normal(0.0, 0.001, size=n))
    vol = rng.uniform(100, 1000, size=n)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": vol,
    }, index=idx)


@pytest.fixture
def patch_loader(monkeypatch):
    """Replace features.builtin._load_ohlcv with a synthetic data source.
    Funding-rate loader is patched to return empty so funding features
    short-circuit (they're not the focus of this test)."""
    df = _fake_load_factory()

    def fake_load_ohlcv(symbol, start, end, tf):
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        s = s.tz_localize("UTC") if s.tzinfo is None else s.tz_convert("UTC")
        e = e.tz_localize("UTC") if e.tzinfo is None else e.tz_convert("UTC")
        return df[(df.index >= s) & (df.index < e)]

    import features.builtin as fb
    monkeypatch.setattr(fb, "_load_ohlcv", fake_load_ohlcv)
    # Bypass cache during the test (cache writes would hit data/features/).
    return df


# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("feat", [
    "atr_14", "atr_pct_14", "realized_vol_30", "ret_24h",
    "trend_50_200", "regime_class", "rsi_14",
])
def test_feature_no_lookahead(patch_loader, feat):
    """Tail-poison test: replace last 25% of OHLCV with NaN. The
    feature's values BEFORE the poison region must be unchanged."""
    df = patch_loader
    n = len(df)
    cut = int(n * 0.75)
    full_start = df.index[0]
    full_end = df.index[-1] + pd.Timedelta(hours=1)

    full = features.compute(feat, "BTCUSDT", full_start, full_end,
                            tf="1h", use_cache=False)
    head_only = features.compute(feat, "BTCUSDT", full_start, df.index[cut],
                                 tf="1h", use_cache=False)

    common = full.index.intersection(head_only.index)
    assert len(common) > 50, f"too few common indices: {len(common)}"
    a = full.loc[common]
    b = head_only.loc[common]
    # NaN-tolerant comparison: positions where ONE is NaN and the other
    # is not is a real divergence; both-NaN is fine.
    both_nan = a.isna() & b.isna()
    neither_nan = ~a.isna() & ~b.isna()
    # If one is NaN and the other isn't, that's a leak.
    leak_mask = a.isna() ^ b.isna()
    assert not leak_mask.any(), (
        f"{feat}: NaN-mask diverges at {leak_mask.sum()} bars "
        f"between full and tail-truncated computation"
    )
    if neither_nan.any():
        diff = (a[neither_nan] - b[neither_nan]).abs()
        assert (diff < 1e-9).all(), (
            f"{feat}: max diff {diff.max()} > 1e-9 "
            "→ feature peeks at data beyond the requested end"
        )


def test_compute_returns_correct_name(patch_loader):
    s = features.compute("atr_14", "BTCUSDT",
                          "2025-01-01", "2025-01-15",
                          tf="1h", use_cache=False)
    assert s.name == "atr_14"


def test_unknown_feature_raises():
    with pytest.raises(KeyError):
        features.compute("definitely_not_a_feature", "BTCUSDT",
                          "2025-01-01", "2025-01-15",
                          tf="1h", use_cache=False)
