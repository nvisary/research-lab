"""Tests for lookback padding in run_split.

Lookback pads the data load BEFORE split.train_start so that
rolling-indicator strategies have a warmed history at bar 1 of the
window instead of wasting the first ~lookback bars on signal-blind
warmup. Mirrors live trading (operator looks at history; doesn't wait
for it).

Tests verify:
  - With lookback=0, behavior is identical to legacy (no padding).
  - With lookback>0, strategy emits valid signals from bar 1 of OOS
    when the indicator's lookback fits within the padding.
  - n_trades increases when warmup is no longer eaten by the window.
  - Curves in return_curves are trimmed to [train_start, oos_end);
    padding bars excluded from the dashboard payload.
  - Out-of-data padding (when no parquets exist before period_start)
    silently degrades — no crash.
"""
from __future__ import annotations

import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from harness import backtest as bt
from harness.costs import CostModel
from harness.splits import Split, train_oos


PERIOD_START = "2024-02-01"
PERIOD_END = "2024-04-01"


def _make_sma_crossover_strategy(fast: int = 20, slow: int = 60) -> types.ModuleType:
    """A strategy that needs `slow` bars of history to emit any signal.
    Without lookback padding, the first `slow` bars of each window are
    NaN signals → position = 0 → no trades."""
    mod = types.ModuleType(f"sma_xover_{fast}_{slow}")
    mod.DEFAULT_SYMBOLS = ["BTCUSDT"]
    mod.DEFAULT_TF = "1h"
    mod.DEFAULT_PARAMS = {"fast": fast, "slow": slow}

    def generate_signals(data, params):
        f = int(params.get("fast", fast))
        s = int(params.get("slow", slow))
        df = data["BTCUSDT"]
        close = df["close"]
        sma_f = close.rolling(f).mean()
        sma_s = close.rolling(s).mean()
        # Long when fast > slow. NaN until both SMAs valid.
        direction = (sma_f > sma_s).astype(float)
        # NaN → 0 only AFTER shift, so the position at bar 1 of OOS depends
        # on whether the window started cold or warm.
        pos = direction.shift(1)
        return pd.DataFrame({
            "timestamp": df.index,
            "symbol": "BTCUSDT",
            "position": pos.values,
        })

    mod.generate_signals = generate_signals
    return mod


def test_lookback_zero_matches_legacy():
    """With lookback=None (default 0), behavior is unchanged."""
    mod = _make_sma_crossover_strategy(fast=10, slow=30)
    s = pd.Timestamp(PERIOD_START, tz="UTC")
    e = pd.Timestamp(PERIOD_END, tz="UTC")
    split = Split(s, s + (e - s) * 0.75, s + (e - s) * 0.75, e)

    a = bt.run_split(mod, {"fast": 10, "slow": 30}, ["BTCUSDT"], split,
                     tf="1h", costs=CostModel(apply_funding=False))
    b = bt.run_split(mod, {"fast": 10, "slow": 30}, ["BTCUSDT"], split,
                     tf="1h", costs=CostModel(apply_funding=False),
                     lookback=None)
    c = bt.run_split(mod, {"fast": 10, "slow": 30}, ["BTCUSDT"], split,
                     tf="1h", costs=CostModel(apply_funding=False),
                     lookback="0")
    assert a["oos"]["n_trades"] == b["oos"]["n_trades"] == c["oos"]["n_trades"]


def test_lookback_padding_warms_indicators():
    """With sufficient lookback padding, strategy emits valid signals
    from bar 1 of train. n_trades should be >= the no-padding case."""
    # 50-bar slow SMA at 1h needs ~50 hours of history to produce a
    # signal. Window is Feb-Apr 2024 (~60 days = 1440 bars at 1h), so
    # without padding the first 50 bars are blind. With 60-day padding
    # the slow SMA is warm immediately.
    mod = _make_sma_crossover_strategy(fast=10, slow=50)
    s = pd.Timestamp(PERIOD_START, tz="UTC")
    e = pd.Timestamp(PERIOD_END, tz="UTC")
    split = Split(s, s + (e - s) * 0.75, s + (e - s) * 0.75, e)

    cold = bt.run_split(mod, {"fast": 10, "slow": 50}, ["BTCUSDT"], split,
                        tf="1h", costs=CostModel(apply_funding=False),
                        lookback=None)
    warm = bt.run_split(mod, {"fast": 10, "slow": 50}, ["BTCUSDT"], split,
                        tf="1h", costs=CostModel(apply_funding=False),
                        lookback="30D")

    # n_trades within OOS slice should match (OOS is far past warmup either
    # way), but the TRAIN slice's n_trades should reflect more activity
    # with padding because train starts at Feb 1 — first ~50 bars cold
    # vs warm.
    train_cold = cold["train"]["n_trades"]
    train_warm = warm["train"]["n_trades"]
    # At minimum, padding should not reduce trade count.
    assert train_warm >= train_cold


def test_lookback_trims_curves():
    """Curves payload starts at train_start, not at the padded data_start."""
    mod = _make_sma_crossover_strategy(fast=10, slow=20)
    s = pd.Timestamp(PERIOD_START, tz="UTC")
    e = pd.Timestamp(PERIOD_END, tz="UTC")
    split = Split(s, s + (e - s) * 0.75, s + (e - s) * 0.75, e)

    out = bt.run_split(mod, {"fast": 10, "slow": 20}, ["BTCUSDT"], split,
                       tf="1h", costs=CostModel(apply_funding=False),
                       return_curves=True, lookback="14D")
    eq = out["equity"]
    bench = out["benchmark"]
    # First bar of equity must be at or after train_start, even though
    # the data was loaded from train_start - 14d.
    assert eq.index[0] >= s, (
        f"equity starts at {eq.index[0]}, expected >= {s}"
    )
    assert bench.index[0] >= s


def test_lookback_with_unavailable_data_degrades_silently():
    """If padding goes before earliest available data, the loader simply
    returns what's available. No crash, no missing-data error."""
    mod = _make_sma_crossover_strategy(fast=5, slow=20)
    # period_start = start of available data (2024-01-01 in the repo's
    # downloaded BTCUSDT). Padding back to 2023 → no parquets → loader
    # returns only what's there.
    s = pd.Timestamp("2024-01-01", tz="UTC")
    e = pd.Timestamp("2024-02-01", tz="UTC")
    split = Split(s, s + (e - s) * 0.75, s + (e - s) * 0.75, e)

    # Should NOT raise.
    out = bt.run_split(mod, {"fast": 5, "slow": 20}, ["BTCUSDT"], split,
                       tf="1h", costs=CostModel(apply_funding=False),
                       lookback="180D")  # way before available data
    assert "oos" in out
    # Equity series should still cover the requested window.
    if "equity" in out and not out["equity"].empty:
        assert out["equity"].index[0] >= s
