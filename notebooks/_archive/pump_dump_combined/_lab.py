"""Shared helpers for manual research notebooks.

Thin convenience layer on top of the project's canonical ``datafeed.loader``.
Import at the top of a notebook (lives in notebooks/pump/) with::

    import sys; sys.path.insert(0, ".")   # cwd = notebooks/pump
    from _lab import *

Keep this train-friendly: it only wraps the loader and adds plotting defaults.
It does NOT touch OOS/holdout artifacts.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Walk up to the repo root (the dir holding pyproject.toml) so this works no
# matter how deep under the repo this file is moved.
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
    """Render the current (or given) figure inline AND save to ``_out/<name>.png``.

    The inline render is for you in Jupyter; the PNG file is what Claude reads
    back (base64 plots embedded in .ipynb are too large to read directly).
    Call at the end of a plotting cell instead of ``plt.show()``.
    """
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
    """All symbols with 1m kline data on disk."""
    return sorted(p.name for p in loader.DATA_ROOT.iterdir() if p.is_dir())


def coverage(symbol: str) -> tuple[str, str, int]:
    """(first_month, last_month, n_months) of available parquet for a symbol."""
    months = sorted(p.stem for p in (loader.DATA_ROOT / symbol).glob("*.parquet"))
    return (months[0], months[-1], len(months)) if months else ("", "", 0)


__all__ = [
    "np", "pd", "plt", "loader",
    "show", "ohlcv", "funding", "list_symbols", "coverage",
]
