"""Regression test: n_trades counts logical round-trips, not partial fills.

vectorbt's ``pf.trades.records_readable`` books one record per partial
fill (every size change inside a position generates a new row via FIFO
slice accounting). For size-varying strategies (vol-target, dynamic
rebalance) this inflates n_trades by 5-15× the true round-trip count,
which:

  - distorts ``composite_score``'s low-trades penalty (it's stuck off
    even when the strategy made only a handful of real trades)
  - makes the trade ledger artefact unreadable (pages of "trades" with
    the same Entry Timestamp and microscopic per-record size)
  - breaks operator intuition ("why does my 17-flip strategy report
    258 trades?") which is exactly how this bug surfaced in holdout.

Fix: use ``pf.positions.records_readable`` (one row per logical
entry → full-exit cycle).

This test exercises the failure mode directly with a synthetic
vol-target-style strategy: a position that enters once, tapers down
across many bars (each taper = one partial fill in pf.trades), then
exits. We assert the harness reports 1 trade, not the partial-fill
count.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import vectorbt as vbt

from harness.backtest import _build_standardized_trades, _run_vectorbt
from harness.costs import CostModel


def _make_taper_portfolio(n_bars: int = 200, taper_start: int = 10,
                          taper_end: int = 50, second_pos: tuple = (70, 110)):
    """Build a vbt Portfolio with one tapered position then a second
    fixed-size position. Returns (pf, expected_round_trips)."""
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="4h", tz="UTC")
    prices = pd.DataFrame(
        {"BTCUSDT": 50_000.0 + np.sin(np.arange(n_bars) / 10) * 1000},
        index=idx,
    )
    target = pd.DataFrame(0.0, index=idx, columns=["BTCUSDT"])
    # First position: tapers from 0.7 down to 0.1 over 40 bars (40 partial fills)
    target.iloc[taper_start:taper_end, 0] = np.linspace(
        0.7, 0.1, taper_end - taper_start
    )
    # Second position: flat 0.5 for 40 bars (no partial fills)
    s2, e2 = second_pos
    target.iloc[s2:e2, 0] = 0.5

    pf = vbt.Portfolio.from_orders(
        close=prices, size=target, size_type="targetpercent",
        init_cash=10_000.0, freq="4h",
    )
    return pf, 2  # exactly two round-trip episodes


def test_pf_trades_is_inflated_by_partial_fills():
    """Sanity: confirm vbt's pf.trades does indeed inflate. If this ever
    flips (vbt API change), our regression target shifts and we should
    re-evaluate the fix's necessity."""
    pf, _ = _make_taper_portfolio()
    n_trades_inflated = len(pf.trades.records_readable)
    n_positions = len(pf.positions.records_readable)
    assert n_trades_inflated > n_positions, (
        f"Expected pf.trades > pf.positions in vbt's accounting; "
        f"got trades={n_trades_inflated}, positions={n_positions}"
    )
    # Concretely: the taper has 40 partial fills + 1 fixed position = 41 trades
    # vs 2 logical round-trips.
    assert n_trades_inflated >= 40
    assert n_positions == 2


def test_standardized_trades_uses_round_trips():
    """The harness ledger reports one row per round-trip, not per fill."""
    pf, expected_round_trips = _make_taper_portfolio()
    train_end = pd.Timestamp("2024-01-01", tz="UTC")  # everything is "oos"
    df = _build_standardized_trades(pf, train_end)
    assert len(df) == expected_round_trips, (
        f"Expected {expected_round_trips} round-trips, got {len(df)} rows"
    )
    # Required columns present.
    assert "entry_time" in df.columns
    assert "exit_time" in df.columns
    assert "size" in df.columns
    assert "pnl_quote" in df.columns
    # Each row's entry_time should be unique (no two round-trips share an entry).
    assert df["entry_time"].nunique() == expected_round_trips


def test_n_trades_counts_round_trips_in_run_split():
    """End-to-end: run_split should report n_trades == round-trips."""
    from harness.backtest import run_split
    from harness.splits import Split

    # Build a strategy module that produces a tapering position followed
    # by a flat one — exactly the vol-target failure pattern.
    import types
    mod = types.ModuleType("taper_strategy")
    mod.DEFAULT_SYMBOLS = ["BTCUSDT"]
    mod.DEFAULT_TF = "4h"
    mod.DEFAULT_PARAMS = {}

    def generate_signals(data, params):
        df = data["BTCUSDT"]
        n = len(df)
        pos = pd.Series(0.0, index=df.index)
        if n >= 110:
            pos.iloc[10:50] = np.linspace(0.7, 0.1, 40)
            pos.iloc[70:110] = 0.5
        # No-lookahead shift handled by harness assumption (signals at t
        # depend only on data <= t-1; for this synthetic test we shift
        # explicitly to satisfy the audit even though harness doesn't run it).
        pos = pos.shift(1).fillna(0.0)
        return pd.DataFrame({
            "timestamp": df.index, "symbol": "BTCUSDT", "position": pos.values,
        })

    mod.generate_signals = generate_signals

    # Need a real BTCUSDT 1m parquet in the data tree; reuse the golden
    # snapshot's fixture window (already present in the repo).
    s = pd.Timestamp("2024-01-01", tz="UTC")
    e = pd.Timestamp("2024-02-01", tz="UTC")  # one month of bars at 4h ≈ 186 bars
    split = Split(train_start=s, train_end=s, oos_start=s, oos_end=e)

    out = run_split(mod, {}, ["BTCUSDT"], split, tf="4h",
                    costs=CostModel(apply_funding=False), return_curves=True)
    n_trades = out["oos"]["n_trades"]
    # Two logical round-trips. Without the fix this would have been ~41.
    assert n_trades == 2, (
        f"Expected 2 round-trips, got {n_trades} — the partial-fill bug "
        f"may have regressed."
    )
    # And the trade ledger has 2 rows, not 41.
    assert len(out["trades"]) == 2
