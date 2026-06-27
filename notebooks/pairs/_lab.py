"""Shared helpers for manual PAIRS-research notebooks.

Thin convenience layer on top of the project's canonical ``datafeed.loader``,
plus a few pair-specific helpers (aligned two-symbol loading, log-spread,
rolling z-score). Import at the top of a notebook (lives in notebooks/pairs/)::

    import sys; sys.path.insert(0, ".")   # cwd = notebooks/pairs
    from _lab import *

Keep this train-friendly: it only wraps the loader and adds plotting/stat
defaults. It does NOT touch OOS/holdout artifacts.
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


# ---- raw loaders (pass-through to datafeed.loader) -------------------------
def ohlcv(symbol: str, start: str, end: str, tf: str = "1min") -> pd.DataFrame:
    """OHLCV for ``symbol`` in [start, end) at timeframe ``tf``."""
    return loader.load(symbol, start, end, tf)


def funding(symbol: str, start: str, end: str) -> pd.DataFrame:
    return loader.load_funding(symbol, start, end)


def open_interest(symbol: str, start: str, end: str) -> pd.DataFrame:
    return loader.load_open_interest(symbol, start, end)


def list_symbols() -> list[str]:
    """All symbols with 1m kline data on disk."""
    return sorted(p.name for p in loader.DATA_ROOT.iterdir() if p.is_dir())


def coverage(symbol: str) -> tuple[str, str, int]:
    """(first_month, last_month, n_months) of available parquet for a symbol."""
    months = sorted(p.stem for p in (loader.DATA_ROOT / symbol).glob("*.parquet"))
    return (months[0], months[-1], len(months)) if months else ("", "", 0)


# ---- pairs-specific helpers ------------------------------------------------
def load_pair(sym_a: str, sym_b: str, start: str, end: str, tf: str = "1h",
              field: str = "close") -> pd.DataFrame:
    """Load two symbols and align them on their common timestamp index.

    Returns a DataFrame with columns ``[a, b]`` (one price ``field`` each,
    default close), inner-joined so every row has both legs present. ``a``/``b``
    map to ``sym_a``/``sym_b``. Use a coarse ``tf`` (1h/4h) for pair analysis
    unless you specifically need 1m granularity.
    """
    da = ohlcv(sym_a, start, end, tf)
    db = ohlcv(sym_b, start, end, tf)
    out = pd.DataFrame({"a": da[field], "b": db[field]}).dropna()
    out.attrs["sym_a"] = sym_a
    out.attrs["sym_b"] = sym_b
    return out


def log_spread(px: pd.DataFrame, beta: float | None = None) -> pd.Series:
    """Log spread of an aligned pair: ``log(a) - beta * log(b)``.

    If ``beta`` is None it is estimated by OLS of log(a) on log(b) over the
    WHOLE window passed in — fine for a quick look, but for any real test
    estimate beta on TRAIN only and apply it forward (no lookahead).
    """
    la, lb = np.log(px["a"]), np.log(px["b"])
    if beta is None:
        beta = float(np.polyfit(lb, la, 1)[0])
    s = la - beta * lb
    s.attrs["beta"] = beta
    return s


def zscore(s: pd.Series, window: int | None = None) -> pd.Series:
    """Z-score of a series. Rolling (causal) if ``window`` given, else full-sample.

    Rolling z = (s - rolling_mean) / rolling_std uses only past bars at each
    point (no lookahead); full-sample z uses the whole window's mean/std and is
    for visualization only.
    """
    if window is None:
        return (s - s.mean()) / s.std()
    m = s.rolling(window).mean()
    sd = s.rolling(window).std()
    return (s - m) / sd


def half_life(s: pd.Series) -> float:
    """Estimate mean-reversion half-life (in bars) via an AR(1) fit on the spread.

    Fits Δs_t = a + b * s_{t-1}; half-life = -ln(2) / ln(1 + b). Returns NaN if
    the series is not mean-reverting (b >= 0). A short half-life relative to your
    holding horizon is the precondition for a pairs trade to work.
    """
    s = s.dropna()
    lag = s.shift(1).dropna()
    ds = (s - s.shift(1)).dropna()
    lag, ds = lag.align(ds, join="inner")
    b = float(np.polyfit(lag.values, ds.values, 1)[0])
    if b >= 0:
        return float("nan")
    return float(-np.log(2) / np.log(1 + b))


__all__ = [
    "np", "pd", "plt", "loader",
    "show", "ohlcv", "funding", "open_interest", "list_symbols", "coverage",
    "load_pair", "log_spread", "zscore", "half_life",
]
