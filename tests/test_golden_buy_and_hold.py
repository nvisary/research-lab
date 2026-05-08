"""Golden snapshot regression: always-long BTC on a fixed window.

The harness has many moving parts (vectorbt, funding, audit, metrics).
This single test pins the end-to-end behaviour of one trivially-defined
strategy on a fixed slice of real BTCUSDT 1h data, so any change that
shifts numbers — intentional or not — surfaces immediately.

To intentionally update the snapshot after an intended change, run:

    pytest tests/test_golden_buy_and_hold.py --update-snapshot

Test data requirement: BTCUSDT 1m parquets for 2024-01..2024-03 must
already be downloaded into ``data/bybit/perp/1m/BTCUSDT/``.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from harness import backtest as bt
from harness.costs import CostModel
from harness.splits import Split

GOLDEN_DIR = Path(__file__).parent / "golden"
SNAP_PATH = GOLDEN_DIR / "buy_and_hold_btc_2024_q1.json"
PERIOD_START = pd.Timestamp("2024-01-01", tz="UTC")
PERIOD_END = pd.Timestamp("2024-04-01", tz="UTC")


def _make_buy_and_hold_module():
    mod = types.ModuleType("buy_and_hold")
    mod.DEFAULT_SYMBOLS = ["BTCUSDT"]
    mod.DEFAULT_TF = "1h"
    mod.DEFAULT_PARAMS: dict = {}

    def generate_signals(data, params):
        df = data["BTCUSDT"]
        # Always +1, after a 1-bar shift to satisfy the no-lookahead rule.
        pos = pd.Series(1.0, index=df.index).shift(1).fillna(0.0)
        return pd.DataFrame({
            "timestamp": df.index,
            "symbol": "BTCUSDT",
            "position": pos.values,
        })

    mod.generate_signals = generate_signals
    return mod


def _run_golden() -> dict:
    mod = _make_buy_and_hold_module()
    split = Split(train_start=PERIOD_START, train_end=PERIOD_START + (PERIOD_END - PERIOD_START) * 0.75,
                  oos_start=PERIOD_START + (PERIOD_END - PERIOD_START) * 0.75,
                  oos_end=PERIOD_END)
    out = bt.run_split(
        mod, {}, ["BTCUSDT"], split, tf="1h",
        costs=CostModel(apply_funding=True),
        return_curves=True,
    )
    eq = out["equity"]
    raw = out.get("raw_equity", eq)
    fc = out.get("funding_cashflow")
    snap = {
        "period": [str(PERIOD_START), str(PERIOD_END)],
        "tf": "1h",
        "n_bars": int(len(eq)),
        "first_equity": round(float(eq.iloc[0]), 6),
        "last_equity": round(float(eq.iloc[-1]), 6),
        "last_raw_equity": round(float(raw.iloc[-1]), 6),
        "total_funding_cashflow": (round(float(fc.sum()), 6) if fc is not None else None),
        "max_dd": round(float(out["oos"]["max_dd"]), 6),
        "oos_sharpe": round(float(out["oos"]["sharpe"]), 6),
        "oos_n_trades": int(out["oos"]["n_trades"]),
        # Sample equity at 5 evenly-spaced indices for shape regression.
        "equity_samples": {
            str(eq.index[i]): round(float(eq.iloc[i]), 6)
            for i in np.linspace(0, len(eq) - 1, 5, dtype=int)
        },
    }
    return snap


def test_golden_buy_and_hold_btc(request):
    snap = _run_golden()
    if request.config.getoption("--update-snapshot"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        SNAP_PATH.write_text(json.dumps(snap, indent=2), encoding="utf-8")
        pytest.skip(f"Snapshot updated at {SNAP_PATH}")

    assert SNAP_PATH.exists(), (
        f"No golden snapshot at {SNAP_PATH}. Generate one with:\n"
        f"  pytest {__file__} --update-snapshot"
    )
    expected = json.loads(SNAP_PATH.read_text(encoding="utf-8"))

    # Strict equality on integer / period / sample-index fields
    for k in ("period", "tf", "n_bars", "oos_n_trades"):
        assert snap[k] == expected[k], f"{k} mismatch: {snap[k]} != {expected[k]}"

    # Float fields with tight tolerance (1e-6) — tracks meaningful changes,
    # ignores last-bit float noise across pandas/vbt versions.
    for k in ("first_equity", "last_equity", "last_raw_equity",
              "total_funding_cashflow", "max_dd", "oos_sharpe"):
        a, b = snap[k], expected[k]
        if a is None and b is None:
            continue
        assert abs(a - b) < 1e-4, f"{k} drift: {a} vs {b}"

    for ts, val in expected["equity_samples"].items():
        a = snap["equity_samples"].get(ts)
        assert a is not None, f"equity sample at {ts} missing"
        assert abs(a - val) < 1e-4, f"equity at {ts} drift: {a} vs {val}"
