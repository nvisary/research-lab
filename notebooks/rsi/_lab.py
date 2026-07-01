"""Shared helpers for the RSI / RSI-divergence research line.

Thin convenience layer on top of the project's canonical ``datafeed.loader``
(same contract as notebooks/pump/_lab.py). Import at the top of a notebook
(lives in notebooks/rsi/) with::

    import sys; sys.path.insert(0, ".")   # cwd = notebooks/rsi
    from _lab import *

Train-friendly: it only wraps the loader, adds plotting defaults, and the two
indicators this line is built on (Wilder RSI + causal swing pivots). It does
NOT touch OOS/holdout artifacts.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Walk up to the repo root (the dir holding pyproject.toml).
REPO = Path(__file__).resolve().parent
for _p in [REPO, *REPO.parents]:
    if (_p / "pyproject.toml").exists():
        REPO = _p
        break
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from datafeed import loader  # noqa: E402

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
    """Render the current (or given) figure inline AND save to ``_out/<name>.png``."""
    _OUT.mkdir(exist_ok=True)
    fig = fig or plt.gcf()
    fig.tight_layout()
    path = _OUT / f"{name}.png"
    fig.savefig(path, dpi=110, bbox_inches="tight")
    print(f"[saved] {path.relative_to(REPO)}")
    return path


def ohlcv(symbol: str, start: str, end: str, tf: str = "1min") -> pd.DataFrame:
    """OHLCV for ``symbol`` in [start, end) at timeframe ``tf``."""
    return loader.load(symbol, start, end, tf)


def funding(symbol: str, start: str, end: str) -> pd.DataFrame:
    return loader.load_funding(symbol, start, end)


def list_symbols() -> list[str]:
    return sorted(p.name for p in loader.DATA_ROOT.iterdir() if p.is_dir())


def coverage(symbol: str) -> tuple[str, str, int]:
    months = sorted(p.stem for p in (loader.DATA_ROOT / symbol).glob("*.parquet"))
    return (months[0], months[-1], len(months)) if months else ("", "", 0)


# ---- indicators ------------------------------------------------------------

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index (the canonical definition).

    RSI = 100 - 100/(1+RS), RS = avg_gain / avg_loss, where the averages use
    Wilder smoothing (an EWMA with alpha = 1/period). This is the same RSI that
    TradingView / most platforms draw by default. Fully causal: RSI[t] uses only
    closes up to and including bar t.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    # When there have been no losses yet, RS -> inf -> RSI = 100; guard NaNs.
    return out.fillna(50.0)


def pivots(series: pd.Series, k: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Causal swing highs / lows confirmed with a ``k``-bar lookback AND lookahead.

    A bar i is a pivot-high if its value is the strict max of the window
    [i-k, i+k]; pivot-low if the strict min. Returns (hi_idx, lo_idx) as integer
    positions.

    IMPORTANT — causality: a pivot at bar i can only be *confirmed* at bar i+k
    (you must see k more bars to know i was the local extreme). So a signal built
    on a pivot is only actionable at i+k, never at i. Any strategy must respect
    this lag; here in a descriptive notebook we draw pivots in-place but always
    remember the confirmation delay. This is exactly the anchor-lookahead trap
    that killed the pump and levels lines.
    """
    v = series.to_numpy()
    n = len(v)
    hi, lo = [], []
    for i in range(k, n - k):
        win = v[i - k:i + k + 1]
        if v[i] == win.max() and (win == v[i]).sum() == 1:
            hi.append(i)
        elif v[i] == win.min() and (win == v[i]).sum() == 1:
            lo.append(i)
    return np.array(hi, dtype=int), np.array(lo, dtype=int)


__all__ = [
    "np", "pd", "plt", "loader",
    "show", "ohlcv", "funding", "list_symbols", "coverage",
    "rsi", "pivots",
]
