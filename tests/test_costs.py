"""Tests for the dynamic cost model.

Static mode is covered indirectly by tests/test_golden_buy_and_hold.py
(snapshot equality with the default CostModel implies static mode is
unchanged). These tests focus on dynamic mode: spread loading, size
impact, fallback, floor.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from harness.costs import CostModel, build_slippage_matrix


def _make_grid(n_bars: int = 100, n_symbols: int = 2,
               freq: str = "1h") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Synthetic price/volume/target_pos with a known structure."""
    idx = pd.date_range("2024-01-01", periods=n_bars, freq=freq, tz="UTC")
    cols = [f"SYM{i}USDT" for i in range(n_symbols)]
    prices = pd.DataFrame(
        {c: 100.0 + np.sin(np.arange(n_bars) / 5) for c in cols},
        index=idx,
    )
    volumes = pd.DataFrame(1000.0, index=idx, columns=cols)
    # target_pos: flip from 0 to 1 at bar 10 then to -1 at bar 50.
    target = pd.DataFrame(0.0, index=idx, columns=cols)
    target.iloc[10:50] = 1.0
    target.iloc[50:] = -1.0
    return prices, volumes, target


# --------------------------------------------------------------------------- #
# Static mode preserves legacy behaviour
# --------------------------------------------------------------------------- #
def test_static_mode_returns_scalar():
    prices, volumes, target = _make_grid()
    costs = CostModel()  # defaults: dynamic off
    out = build_slippage_matrix(prices, volumes, target, init_cash=10_000.0, costs=costs)
    assert isinstance(out, float)
    assert out == pytest.approx(1.0 * 1e-4)  # default slippage_bps=1.0


def test_static_mode_respects_custom_slippage():
    prices, volumes, target = _make_grid()
    costs = CostModel(slippage_bps=3.0)
    out = build_slippage_matrix(prices, volumes, target, init_cash=10_000.0, costs=costs)
    assert out == pytest.approx(3.0 * 1e-4)


# --------------------------------------------------------------------------- #
# Dynamic mode — fallback when no spread parquets exist for the symbol
# --------------------------------------------------------------------------- #
def test_dynamic_spread_falls_back_when_no_parquet(tmp_path, monkeypatch):
    """Symbol with no saved spread parquets: matrix is constant at
    `slippage_bps`, which is also the fallback."""
    import datafeed.spreads as sp
    monkeypatch.setattr(sp, "_spread_root", lambda: tmp_path / "spreads")

    prices, volumes, target = _make_grid()
    costs = CostModel(use_dynamic_spread=True, slippage_bps=2.0)
    out = build_slippage_matrix(prices, volumes, target, init_cash=10_000.0, costs=costs)
    assert isinstance(out, pd.DataFrame)
    assert out.shape == prices.shape
    # half_spread = fallback_bps * spread_to_slippage_ratio = 2.0 * 0.5 = 1.0 bps.
    # Floor is min_slippage_bps=0.5 — doesn't bind.
    expected = 1.0 * 1e-4
    np.testing.assert_allclose(out.values, expected)


# --------------------------------------------------------------------------- #
# Dynamic mode — uses saved spreads when available
# --------------------------------------------------------------------------- #
def _seed_spread_parquet(spread_root, symbol: str, ym: tuple[int, int],
                         spread_bps: float) -> None:
    """Write a synthetic spread parquet covering one month."""
    y, m = ym
    start = pd.Timestamp(f"{y:04d}-{m:02d}-01", tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    bucket_starts = pd.date_range(start, end, freq="1h", tz="UTC")[:-1]
    df = pd.DataFrame({
        "bucket_start": bucket_starts,
        "spread_bps": spread_bps,
        "n_bars": 60,
        "fallback_used": False,
    })
    out = spread_root / symbol / f"{y:04d}-{m:02d}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, compression="zstd", index=False)


def test_dynamic_spread_uses_saved_parquets(tmp_path, monkeypatch):
    spread_root = tmp_path / "spreads"
    import datafeed.spreads as sp
    monkeypatch.setattr(sp, "_spread_root", lambda: spread_root)

    prices, volumes, target = _make_grid()
    # SYM0USDT: saved spread of 4 bps. SYM1USDT: no parquet → fallback.
    _seed_spread_parquet(spread_root, "SYM0USDT", (2024, 1), spread_bps=4.0)

    costs = CostModel(use_dynamic_spread=True, slippage_bps=10.0)
    out = build_slippage_matrix(prices, volumes, target, init_cash=10_000.0, costs=costs)

    # SYM0: 4 bps spread × 0.5 ratio = 2 bps half-spread.
    np.testing.assert_allclose(out["SYM0USDT"].values, 2.0 * 1e-4)
    # SYM1: 10 bps fallback × 0.5 = 5 bps.
    np.testing.assert_allclose(out["SYM1USDT"].values, 5.0 * 1e-4)


# --------------------------------------------------------------------------- #
# Size impact
# --------------------------------------------------------------------------- #
def test_size_impact_scales_with_order_size(tmp_path, monkeypatch):
    spread_root = tmp_path / "spreads"
    import datafeed.spreads as sp
    monkeypatch.setattr(sp, "_spread_root", lambda: spread_root)

    prices, volumes, target = _make_grid(n_bars=200, n_symbols=1)
    # Saved spread = 0 → spread component is just floor; isolates size impact.
    _seed_spread_parquet(spread_root, "SYM0USDT", (2024, 1), spread_bps=0.0)

    costs = CostModel(
        use_dynamic_spread=True,
        use_dynamic_slippage=True,
        slippage_size_k=1.0,
        size_impact_window=20,
        size_impact_cap_bps=10000.0,   # don't cap
        min_slippage_bps=0.0,
    )
    out = build_slippage_matrix(prices, volumes, target, init_cash=10_000.0, costs=costs)

    # Order at bar 10 (target 0 → 1, |Δ|=1): order_size = init_cash/1 * 1 = $10000.
    # Depth = mean(volume * close) * window = 1000 * ~100 * 20 = ~2_000_000.
    # Size impact bps = k * 10000 / 2_000_000 * 1e4 = 1.0 * 50 = 50 bps.
    bar_10_slippage_bps = float(out["SYM0USDT"].iloc[10]) * 1e4
    assert 30 < bar_10_slippage_bps < 70, f"got {bar_10_slippage_bps} bps"

    # Bars in [11, 49] no rebalance happens → size impact ≈ 0, slippage = floor.
    assert float(out["SYM0USDT"].iloc[20]) * 1e4 == pytest.approx(0.0, abs=0.1)


def test_size_impact_caps_at_threshold(tmp_path, monkeypatch):
    spread_root = tmp_path / "spreads"
    import datafeed.spreads as sp
    monkeypatch.setattr(sp, "_spread_root", lambda: spread_root)

    # Tiny depth → uncapped slippage would explode.
    idx = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")
    prices = pd.DataFrame({"SYM0USDT": 100.0}, index=idx)
    volumes = pd.DataFrame({"SYM0USDT": 0.01}, index=idx)   # near-zero depth
    target = pd.DataFrame(0.0, index=idx, columns=["SYM0USDT"])
    target.iloc[10:] = 1.0  # one rebalance

    _seed_spread_parquet(spread_root, "SYM0USDT", (2024, 1), spread_bps=0.0)

    costs = CostModel(
        use_dynamic_spread=True,
        use_dynamic_slippage=True,
        slippage_size_k=1.0,
        size_impact_window=20,
        size_impact_cap_bps=42.0,  # cap chosen to verify
        min_slippage_bps=0.0,
    )
    out = build_slippage_matrix(prices, volumes, target, init_cash=10_000.0, costs=costs)
    bar_10_bps = float(out["SYM0USDT"].iloc[10]) * 1e4
    assert bar_10_bps == pytest.approx(42.0, abs=0.01)


# --------------------------------------------------------------------------- #
# Floor
# --------------------------------------------------------------------------- #
def test_floor_applied_in_dynamic_mode(tmp_path, monkeypatch):
    spread_root = tmp_path / "spreads"
    import datafeed.spreads as sp
    monkeypatch.setattr(sp, "_spread_root", lambda: spread_root)

    prices, volumes, target = _make_grid()
    _seed_spread_parquet(spread_root, "SYM0USDT", (2024, 1), spread_bps=0.0)
    _seed_spread_parquet(spread_root, "SYM1USDT", (2024, 1), spread_bps=0.0)

    costs = CostModel(use_dynamic_spread=True, min_slippage_bps=0.7)
    out = build_slippage_matrix(prices, volumes, target, init_cash=10_000.0, costs=costs)
    # Spread = 0 × 0.5 = 0 bps → floored to 0.7.
    np.testing.assert_allclose(out.values, 0.7 * 1e-4)


# --------------------------------------------------------------------------- #
# is_dynamic flag
# --------------------------------------------------------------------------- #
def test_is_dynamic_flag():
    assert not CostModel().is_dynamic
    assert CostModel(use_dynamic_spread=True).is_dynamic
    assert CostModel(use_dynamic_slippage=True).is_dynamic
    assert CostModel(use_dynamic_spread=True, use_dynamic_slippage=True).is_dynamic
