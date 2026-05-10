"""Tests for the position sizing modes: default vs RAW_SIZING, and the
MAX_POSITION clip. Pins the contract that:

  - Default mode (RAW_SIZING absent or False): position field is "fraction
    of equal-weight slot"; harness divides by n_symbols before passing to
    vectorbt's targetpercent.
  - RAW_SIZING=True: position field is "fraction of total equity" directly.
  - MAX_POSITION caps per-asset position; total portfolio exposure is
    still capped at 100pct by vectorbt's cash_sharing config.
  - Legacy strategies that set neither attribute behave exactly as before.
"""
from __future__ import annotations

import types

import pandas as pd

from harness import backtest as bt
from harness.costs import CostModel
from harness.splits import Split


def _make(per_position: float, symbols: list[str],
          raw_sizing: bool | None = None,
          max_position: float | None = None) -> types.ModuleType:
    """Build a synthetic strategy that emits the same constant position
    on every bar for every symbol. Optional sizing attributes are added
    only if explicitly requested — None leaves the attribute absent so
    the harness defaults take effect."""
    m = types.ModuleType("test_sizing_strategy")
    m.DEFAULT_SYMBOLS = symbols
    m.DEFAULT_TF = "1d"
    m.DEFAULT_PARAMS = {}
    m.PARAM_SPACE = {}
    if raw_sizing is not None:
        m.RAW_SIZING = raw_sizing
    if max_position is not None:
        m.MAX_POSITION = max_position

    def gen(data, params):
        rows = []
        for s, df in data.items():
            rows.append(pd.DataFrame({
                "timestamp": df.index, "symbol": s,
                "position": [per_position] * len(df),
            }))
        return pd.concat(rows, ignore_index=True)
    m.generate_signals = gen
    return m


def _run(mod, syms):
    s = pd.Timestamp("2024-06-01", tz="UTC")
    e = pd.Timestamp("2024-12-31", tz="UTC")
    split = Split(s, s + (e - s) * 0.75, s + (e - s) * 0.75, e)
    return bt.run_split(mod, {}, syms, split, tf="1d",
                         costs=CostModel(apply_funding=False),
                         return_curves=True, lookback=None)


def test_legacy_sizing_default():
    """Legacy strategy without RAW_SIZING attribute: position=1 per asset
    on a 3-asset basket → 33pct each, 100pct total."""
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    out = _run(_make(1.0, syms), syms)
    pnl = float(out["equity"].iloc[-1] - out["equity"].iloc[0])
    # Sanity: the basket made money in this period — not asserting a
    # specific number (depends on data) but it should be positive and
    # non-trivial.
    assert pnl > 0, f"basket lost money in 2024-H2 (sanity-failure): {pnl}"


def test_raw_sizing_matches_default_when_scaled():
    """RAW_SIZING with position = 1/n is equivalent to default with
    position = 1 — both result in 1/n equity allocation per asset."""
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    n = len(syms)
    legacy = _run(_make(1.0, syms), syms)
    raw = _run(_make(1.0 / n, syms, raw_sizing=True), syms)

    legacy_eq = float(legacy["equity"].iloc[-1])
    raw_eq = float(raw["equity"].iloc[-1])
    # Allow tiny numerical drift from different code paths.
    assert abs(legacy_eq - raw_eq) < 1.0, (
        f"RAW_SIZING with position={1/n} should match default with position=1: "
        f"legacy {legacy_eq} vs raw {raw_eq}"
    )


def test_raw_sizing_single_asset_kelly_like():
    """RAW_SIZING is the natural mode for single-asset Kelly: position=0.5
    means 50pct of equity in the asset."""
    syms = ["BTCUSDT"]
    out_full = _run(_make(1.0, syms, raw_sizing=True), syms)
    out_half = _run(_make(0.5, syms, raw_sizing=True), syms)

    full_pnl = float(out_full["equity"].iloc[-1] - 10_000)
    half_pnl = float(out_half["equity"].iloc[-1] - 10_000)
    # Half position → roughly half PnL (allow ±5pct for fees scaling).
    ratio = half_pnl / full_pnl if full_pnl else 0
    assert 0.45 < ratio < 0.55, (
        f"half position should give ~half PnL: full {full_pnl}, half {half_pnl}, "
        f"ratio {ratio}"
    )


def test_max_position_default_is_one():
    """Default MAX_POSITION=1.0: emitting position=2.0 gets clipped to 1.0,
    so it produces the same result as position=1.0."""
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    out_one = _run(_make(1.0, syms), syms)
    out_two = _run(_make(2.0, syms), syms)  # should be clipped to 1.0
    one = float(out_one["equity"].iloc[-1])
    two = float(out_two["equity"].iloc[-1])
    assert abs(one - two) < 1.0, (
        f"position=2 should clip to 1 by default: one {one} vs two {two}"
    )


def test_max_position_above_one_allows_oversize_per_asset():
    """MAX_POSITION=2.0 in raw mode lets a single-asset position go to
    150pct of equity. vectorbt's cash_sharing caps total exposure at
    100pct, so the realised PnL won't actually scale linearly above
    100pct — but the strategy's intent is preserved (clip at 2.0 not 1.0)."""
    syms = ["BTCUSDT"]
    # Without MAX_POSITION override, position=1.5 clips to 1.0 → same as 1.0.
    out_clipped = _run(_make(1.5, syms, raw_sizing=True), syms)
    # With MAX_POSITION=2.0, the clip permits 1.5 — vectorbt then handles it
    # (effectively still ~100pct due to cash_sharing, but no harness clip).
    out_raised = _run(_make(1.5, syms, raw_sizing=True, max_position=2.0), syms)

    # Both should produce non-zero PnL; raised cap shouldn't crash.
    a = float(out_clipped["equity"].iloc[-1] - 10_000)
    b = float(out_raised["equity"].iloc[-1] - 10_000)
    assert a != 0
    assert b != 0
    # Neither should be wildly different — vectorbt caps total at 100pct
    # cash, so a 150pct target produces about the same as a 100pct one.
    # We just pin that raising MAX_POSITION doesn't crash and produces
    # a sensible (positive in this period) PnL.
    assert b > 0
