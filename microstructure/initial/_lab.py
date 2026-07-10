"""Shared helpers for microstructure research notebooks (microstructure/initial/).

Thin convenience layer over ``microstructure.loader``. Import at the top of a
notebook with::

    import sys; sys.path.insert(0, ".")   # cwd = microstructure/initial
    from _lab import *

Same spirit as ``notebooks/*/_lab.py``: wraps the loader, adds plotting defaults
and a ``show()`` that saves ``_out/<name>.png`` so Claude can read plots back.
This is microstructure data (order book / trades / ticker / liquidations),
NOT the 1m OHLCV parquet used by ``notebooks/``.
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

from microstructure import loader  # noqa: E402

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


# ---- loader passthroughs ---------------------------------------------------
list_sessions = loader.list_sessions
latest_session = loader.latest_session
manifest = loader.manifest
load = loader.load


def session_symbols(session_id: str, stream: str = "orderbook") -> list[str]:
    return loader.symbols(session_id, stream)


# ---- microstructure primitives (top-of-book) -------------------------------
def mid(ob: pd.DataFrame) -> pd.Series:
    """Mid price = (best bid + best ask) / 2, indexed like ``ob``."""
    return (ob["bid_px_0"] + ob["ask_px_0"]) / 2.0


def spread(ob: pd.DataFrame) -> pd.Series:
    """Absolute top-of-book spread (ask0 - bid0)."""
    return ob["ask_px_0"] - ob["bid_px_0"]


def spread_bps(ob: pd.DataFrame) -> pd.Series:
    """Spread in basis points of mid (1 bp = 0.01%)."""
    return 1e4 * spread(ob) / mid(ob)


def depth(ob: pd.DataFrame, levels: int = 25, side: str = "bid") -> pd.Series:
    """Summed size over the top ``levels`` on one side (available columns only)."""
    cols = [f"{side}_sz_{i}" for i in range(levels) if f"{side}_sz_{i}" in ob.columns]
    return ob[cols].sum(axis=1)


def imbalance(ob: pd.DataFrame, levels: int = 5) -> pd.Series:
    """Order-book imbalance in [-1, 1] over top ``levels``:
    (bid_size - ask_size) / (bid_size + ask_size).  >0 = bid-heavy (buy pressure).
    """
    b = depth(ob, levels, "bid")
    a = depth(ob, levels, "ask")
    return (b - a) / (b + a)


def feed_lag_ms(df: pd.DataFrame) -> pd.Series:
    """Per-row feed lag = ts_local - ts_exchange (ms)."""
    return df["ts_local"] - df["ts_exchange"]
