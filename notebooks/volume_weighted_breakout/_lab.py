"""Shared helpers for volume_weighted_breakout notebooks.

Thin convenience layer around the project's canonical strategy loader,
data loader, and backtest harness. Import from notebooks in this directory::

    import sys; sys.path.insert(0, ".")   # cwd = notebooks/volume_weighted_breakout
    from _lab import *

Keep this diagnostic-friendly: helpers run the current checked-out strategy and
do not write verdict artifacts.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from shutil import copy2

import numpy as np
import pandas as pd


REPO = Path(__file__).resolve().parent
for _p in [REPO, *REPO.parents]:
    if (_p / "pyproject.toml").exists():
        REPO = _p
        break
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from datafeed import loader  # noqa: E402
from harness.backtest import load_strategy, run as harness_run  # noqa: E402


STRATEGY_DIR = REPO / "strategies" / "volume_weighted_breakout"
RUNS_DIR = STRATEGY_DIR / "runs"
BEST_JSON = RUNS_DIR / "best.json"
BEST_STRATEGY_FILE = RUNS_DIR / "best_strategy.py"
strategy = load_strategy(STRATEGY_DIR)


# ---- plotting defaults -----------------------------------------------------
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams.update({
    "figure.figsize": (11, 4.2),
    "figure.dpi": 110,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "font.size": 10,
})


_OUT = Path(__file__).resolve().parent / "_out"


def show(name: str = "plot", fig=None):
    """Render the current or given figure inline and save to ``_out/<name>.png``."""
    _OUT.mkdir(exist_ok=True)
    fig = fig or plt.gcf()
    fig.tight_layout()
    path = _OUT / f"{name}.png"
    fig.savefig(path, dpi=110, bbox_inches="tight")
    print(f"[saved] {path.relative_to(REPO)}")
    return path


# ---- data and strategy helpers --------------------------------------------
def ohlcv(symbol: str, start: str, end: str, tf: str | None = None) -> pd.DataFrame:
    """OHLCV for ``symbol`` in [start, end) at the strategy TF by default."""
    return loader.load(symbol, start, end, tf or strategy.DEFAULT_TF)


def list_symbols() -> list[str]:
    """All symbols with 1m kline data on disk."""
    return sorted(p.name for p in loader.DATA_ROOT.iterdir() if p.is_dir())


def coverage(symbol: str) -> tuple[str, str, int]:
    """(first_month, last_month, n_months) of available parquet for a symbol."""
    months = sorted(p.stem for p in (loader.DATA_ROOT / symbol).glob("*.parquet"))
    return (months[0], months[-1], len(months)) if months else ("", "", 0)


def load_pair_data(symbols: list[str], start: str, end: str,
                   tf: str | None = None) -> dict[str, pd.DataFrame]:
    """Load two or more symbols as the strategy expects: {symbol: ohlcv_df}."""
    use_tf = tf or strategy.DEFAULT_TF
    return {sym: ohlcv(sym, start, end, use_tf) for sym in symbols}


def generate_pair_signals(symbols: list[str], start: str, end: str,
                          params: dict | None = None,
                          tf: str | None = None) -> pd.DataFrame:
    """Run strategy.generate_signals for a small symbol list."""
    p = dict(strategy.DEFAULT_PARAMS)
    if params:
        p.update(params)
    data = load_pair_data(symbols, start, end, tf=tf)
    return strategy.generate_signals(data, p)


def run_pair(symbols: list[str], start: str, end: str,
             params: dict | None = None,
             tf: str | None = None,
             walk: int = 0,
             return_curves: bool = True) -> dict:
    """Run the current strategy through the canonical backtest harness."""
    return harness_run(
        STRATEGY_DIR,
        start,
        end,
        symbols=symbols,
        tf=tf or strategy.DEFAULT_TF,
        params=params,
        walk_windows=walk,
        return_curves=return_curves,
    )


def run_strategy_file(strategy_file: str | Path,
                      symbols: list[str],
                      start: str,
                      end: str,
                      params: dict | None = None,
                      tf: str | None = None,
                      walk: int = 0,
                      return_curves: bool = True) -> dict:
    """Run a standalone strategy file through the canonical harness.

    The harness expects a directory containing ``strategy.py``. For archived
    run artifacts such as ``runs/best_strategy.py``, copy the file into a
    temporary harness-shaped directory and run it there.
    """
    strategy_file = Path(strategy_file)
    with tempfile.TemporaryDirectory(prefix="vwb_replay_") as tmp:
        tmp_dir = Path(tmp)
        copy2(strategy_file, tmp_dir / "strategy.py")
        return harness_run(
            tmp_dir,
            start,
            end,
            symbols=symbols,
            tf=tf,
            params=params,
            walk_windows=walk,
            return_curves=return_curves,
        )


def positions_wide(signals: pd.DataFrame) -> pd.DataFrame:
    """Pivot long-format strategy signals into timestamp x symbol positions."""
    if signals.empty:
        return pd.DataFrame()
    s = signals.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True)
    return s.pivot_table(
        index="timestamp",
        columns="symbol",
        values="position",
        aggfunc="last",
    ).sort_index().ffill().fillna(0.0)


def metrics_frame(result: dict) -> pd.DataFrame:
    """Compact train/OOS metric table from a harness result."""
    rows = []
    for split in ("train", "oos"):
        m = result["main"].get(split, {})
        rows.append({
            "split": split,
            "sharpe": m.get("sharpe"),
            "sortino": m.get("sortino"),
            "max_dd": m.get("max_dd"),
            "total_return": m.get("total_return"),
            "n_trades": m.get("n_trades"),
            "pct_time_in_position": m.get("pct_time_in_position"),
            "profit_factor": m.get("profit_factor"),
        })
    return pd.DataFrame(rows).set_index("split")


def rebase(series: pd.Series, start_value: float = 1000.0) -> pd.Series:
    """Scale an equity-like series to start at ``start_value``."""
    s = series.dropna().astype(float)
    if s.empty:
        return s
    return s / float(s.iloc[0]) * float(start_value)


def drawdown(equity: pd.Series) -> pd.Series:
    """Drawdown series as a negative fraction from prior equity peak."""
    eq = equity.dropna().astype(float)
    if eq.empty:
        return eq
    return eq / eq.cummax() - 1.0


def monthly_returns(equity: pd.Series) -> pd.Series:
    """Calendar-month returns from an equity curve."""
    eq = equity.dropna().astype(float)
    if eq.empty:
        return eq
    return eq.resample("ME").last().pct_change().dropna()


def clean_metrics(obj):
    """Convert nested metrics payloads to notebook-display-friendly Python types."""
    if isinstance(obj, dict):
        return {k: clean_metrics(v) for k, v in obj.items()
                if k not in {"equity", "benchmark", "raw_equity", "oos_returns", "trades"}}
    if isinstance(obj, list):
        return [clean_metrics(v) for v in obj]
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


__all__ = [
    "np", "pd", "plt", "loader", "strategy",
    "STRATEGY_DIR", "RUNS_DIR", "BEST_JSON", "BEST_STRATEGY_FILE",
    "show", "ohlcv", "list_symbols", "coverage",
    "load_pair_data", "generate_pair_signals", "run_pair", "run_strategy_file",
    "positions_wide", "metrics_frame", "rebase", "drawdown",
    "monthly_returns", "clean_metrics",
]
