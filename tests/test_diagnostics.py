"""Tests for harness.diagnostics — pin the contract for the rich
per-iter JSON: structure, flag heuristics, best-effort failure modes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from harness.diagnostics import build_diagnostics


def _make_runs_dir(tmp_path: Path, history: list[dict],
                   equity_df: pd.DataFrame | None = None,
                   trades_df: pd.DataFrame | None = None,
                   iter_id: int = 1) -> Path:
    runs = tmp_path / "runs"
    (runs).mkdir(parents=True)

    if history:
        h = runs / "history.jsonl"
        with h.open("w", encoding="utf-8") as f:
            for row in history:
                f.write(json.dumps(row) + "\n")

    if equity_df is not None:
        eq = runs / "equity"
        eq.mkdir()
        equity_df.to_parquet(eq / f"iter_{iter_id:04d}.parquet", index=False)

    if trades_df is not None:
        tr = runs / "trades"
        tr.mkdir()
        trades_df.to_parquet(tr / f"iter_{iter_id:04d}.parquet", index=False)

    return runs


def _toy_equity(n_windows: int = 4, bars_per_window: int = 100,
                final_per_window: list[float] | None = None) -> pd.DataFrame:
    """Build a synthetic equity parquet with `n_windows` chunks.
    Each window starts at $10000 and ends at final_per_window[i]."""
    if final_per_window is None:
        final_per_window = [10500] * n_windows
    rows = []
    for w in range(n_windows):
        start_ts = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=w * 30)
        ts = pd.date_range(start_ts, periods=bars_per_window, freq="1D")
        eq = pd.Series([10000 + i * (final_per_window[w] - 10000) / (bars_per_window - 1)
                        for i in range(bars_per_window)])
        rows.append(pd.DataFrame({
            "timestamp": ts, "equity": eq.values,
            "raw_equity": eq.values, "funding_cashflow": 0.0,
            "window": w,
        }))
    return pd.concat(rows, ignore_index=True)


def _toy_trades(n_winners: int = 5, n_losers: int = 3,
                win_pnl: float = 100, loss_pnl: float = -50) -> pd.DataFrame:
    """Build a synthetic trade ledger."""
    rows = []
    for i in range(n_winners):
        rows.append({
            "entry_time": pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=i),
            "exit_time": pd.Timestamp("2024-01-02", tz="UTC") + pd.Timedelta(days=i),
            "symbol": "BTCUSDT", "direction": "long",
            "size": 1.0, "entry_price": 50000, "exit_price": 50500,
            "pnl_quote": float(win_pnl), "return_pct": 0.01,
            "duration_hours": 24.0, "slice": "oos", "window": 0,
        })
    for i in range(n_losers):
        rows.append({
            "entry_time": pd.Timestamp("2024-02-01", tz="UTC") + pd.Timedelta(days=i),
            "exit_time": pd.Timestamp("2024-02-02", tz="UTC") + pd.Timedelta(days=i),
            "symbol": "BTCUSDT", "direction": "long",
            "size": 1.0, "entry_price": 50000, "exit_price": 49750,
            "pnl_quote": float(loss_pnl), "return_pct": -0.005,
            "duration_hours": 24.0, "slice": "oos", "window": 0,
        })
    return pd.DataFrame(rows)


def test_per_window_block_structure(tmp_path):
    """diagnostics["windows"] mirrors result.walk_forward.windows with
    train_sh, oos_sh, gap, trades, dd computed correctly."""
    runs = _make_runs_dir(tmp_path, history=[])
    result = {
        "walk_forward": {
            "windows": [
                {"train": {"sharpe": 2.0}, "oos": {"sharpe": 1.0, "max_dd": 0.05, "n_trades": 10}},
                {"train": {"sharpe": 0.5}, "oos": {"sharpe": 1.5, "max_dd": 0.03, "n_trades": 12}},
            ]
        }
    }
    summary = {"oos_sharpe": 1.25, "oos_n_trades": 22}
    diag = build_diagnostics(1, runs, summary, result, dsr_value=0.5)

    assert "windows" in diag
    assert len(diag["windows"]) == 2
    assert diag["windows"][0]["train_sh"] == 2.0
    assert diag["windows"][0]["oos_sh"] == 1.0
    assert diag["windows"][0]["gap"] == 1.0  # 2.0 - 1.0
    assert diag["windows"][1]["gap"] == -1.0  # 0.5 - 1.5


def test_trajectory_dsr_delta(tmp_path):
    """When DSR has dropped from a higher peak, trajectory.dsr_delta_from_best
    is negative and a flag is emitted at >= 0.2 drop."""
    history = [
        {"iter": 1, "dsr": 0.72, "composite": 1.50},
        {"iter": 2, "dsr": 0.55, "composite": 1.55},
        {"iter": 3, "dsr": 0.41, "composite": 1.58},
    ]
    runs = _make_runs_dir(tmp_path, history=history)
    result = {"walk_forward": {"windows": []}}
    summary = {"oos_sharpe": 2.0, "oos_n_trades": 60}
    diag = build_diagnostics(3, runs, summary, result, dsr_value=0.41)

    assert diag["trajectory"]["dsr_best"] == 0.72
    assert diag["trajectory"]["dsr_now"] == 0.41
    assert diag["trajectory"]["dsr_delta_from_best"] == pytest.approx(-0.31, abs=0.01)
    flags = " ".join(diag["flags"])
    assert "DSR down" in flags


def test_stitched_negative_with_positive_oos_sharpe_flagged(tmp_path):
    """The selection-bias trap (positive OOS Sharpe but negative 24mo
    stitched return) emits a double-warn flag."""
    eq_df = _toy_equity(n_windows=4, final_per_window=[9500, 9700, 9600, 9700])
    runs = _make_runs_dir(tmp_path, history=[], equity_df=eq_df, iter_id=1)
    result = {
        "walk_forward": {
            "windows": [
                {"train": {"sharpe": 0.0}, "oos": {"sharpe": 1.0, "max_dd": 0.05, "n_trades": 10}}
                for _ in range(4)
            ]
        }
    }
    summary = {"oos_sharpe": 1.0, "oos_n_trades": 40}
    diag = build_diagnostics(1, runs, summary, result, dsr_value=0.5)

    assert diag["stitched"]["compounded_return_pct"] < 0
    flags = " ".join(diag["flags"])
    assert "edge lives in OOS slices only" in flags


def test_fat_tail_flag_when_one_trade_dominates(tmp_path):
    """If a single trade is >30% of total |PnL|, fat-tail flag fires."""
    # 1 huge winner of $1000, 9 small losers of -$10 each.
    # Total = 1000 - 90 = $910. Largest trade = $1000 = 110% of |total|.
    rows = [
        {"entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
         "exit_time": pd.Timestamp("2024-01-02", tz="UTC"),
         "symbol": "BTC", "direction": "long",
         "size": 1.0, "entry_price": 1, "exit_price": 1,
         "pnl_quote": 1000.0, "return_pct": 0.5,
         "duration_hours": 24.0, "slice": "oos", "window": 0},
    ]
    for i in range(9):
        rows.append({
            "entry_time": pd.Timestamp("2024-02-01", tz="UTC") + pd.Timedelta(days=i),
            "exit_time": pd.Timestamp("2024-02-02", tz="UTC") + pd.Timedelta(days=i),
            "symbol": "BTC", "direction": "long",
            "size": 1.0, "entry_price": 1, "exit_price": 1,
            "pnl_quote": -10.0, "return_pct": -0.001,
            "duration_hours": 24.0, "slice": "oos", "window": 0,
        })
    tr_df = pd.DataFrame(rows)
    runs = _make_runs_dir(tmp_path, history=[], trades_df=tr_df, iter_id=1)

    result = {"walk_forward": {"windows": []}}
    summary = {"oos_sharpe": 1.5, "oos_n_trades": 10}
    diag = build_diagnostics(1, runs, summary, result, dsr_value=0.4)

    assert diag["shape"]["largest_trade_pct_of_total"] > 30
    flags = " ".join(diag["flags"])
    assert "fat-tail" in flags


def test_best_effort_no_parquets(tmp_path):
    """When equity/trades parquets are absent, diagnostics still
    produces a valid dict with windows/trajectory/flags only."""
    runs = _make_runs_dir(tmp_path, history=[])
    result = {
        "walk_forward": {
            "windows": [
                {"train": {"sharpe": 1.0},
                 "oos": {"sharpe": 0.5, "max_dd": 0.1, "n_trades": 5}}
            ]
        }
    }
    summary = {"oos_sharpe": 0.5, "oos_n_trades": 5}
    diag = build_diagnostics(1, runs, summary, result, dsr_value=0.3)
    # Basic structure present, parquet-derived sections absent.
    assert "windows" in diag
    assert "stitched" not in diag
    assert "monthly" not in diag
    assert "shape" not in diag
    assert "flags" in diag


def test_legacy_strategy_no_history_no_crash(tmp_path):
    """First iter (no history yet, no parquets) — should still produce
    a valid dict without crashing."""
    runs = tmp_path / "runs"
    runs.mkdir(parents=True)
    result = {"walk_forward": None}
    summary = {"oos_sharpe": 1.0, "oos_n_trades": 50}
    diag = build_diagnostics(1, runs, summary, result, dsr_value=0.5)
    assert isinstance(diag, dict)
    assert "flags" in diag
