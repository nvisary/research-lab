"""Shared helpers for the STATARB research line (notebooks/statarb/).

Deliberately panel-oriented: statistical arbitrage on a basket lives or dies on
a wide price matrix (rows = timestamps, cols = symbols), not on one-symbol
DataFrames. A pair is just the N=2 case of a basket, so every helper here takes
a weight VECTOR over K legs.

Import at the top of a notebook (cwd = notebooks/statarb)::

    import sys; sys.path.insert(0, ".")
    from _lab import *

Two hard rules encoded here:

1. **Train/OOS split.** ``TRAIN`` is the only window we may look at while
   designing. ``OOS`` is locked behind ``oos_slice(..., unlock="reason")`` so
   that reading it is always a deliberate, logged act.
2. **Costs are per unit of GROSS NOTIONAL traded**, not per leg. See
   ``cost_bps`` — this distinction decides whether multi-leg baskets are
   actually more expensive than pairs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---- repo discovery --------------------------------------------------------
REPO = Path(__file__).resolve().parent
for _p in [REPO, *REPO.parents]:
    if (_p / "pyproject.toml").exists():
        REPO = _p
        break
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from datafeed import loader  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "_out"
OUT.mkdir(exist_ok=True)

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


def show(name: str = "plot", fig=None):
    """Render the current figure inline AND save ``_out/<name>.png``.

    The inline render is for the human in Jupyter; the PNG is what Claude reads
    back as an image. Call instead of ``plt.show()``.
    """
    fig = fig or plt.gcf()
    fig.tight_layout()
    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=110, bbox_inches="tight")
    print(f"[saved] {path.relative_to(REPO)}")
    return path


# ---- research windows ------------------------------------------------------
# Data on disk is gap-free for 2024-01..2026-04 across ~140 symbols (2026-05 is
# missing for 59 symbols), so the usable full window ends 2026-05-01 exclusive.
FULL = ("2024-01-01", "2026-05-01")
TRAIN = ("2024-01-01", "2025-07-01")   # design/fit here — free to look
OOS = ("2025-07-01", "2026-05-01")     # locked until a rule is frozen

FULL_MONTHS = [str(p)[:7] for p in pd.period_range("2024-01", "2026-04", freq="M")]


def train_slice(df: pd.DataFrame | pd.Series):
    """Rows of a time-indexed object inside the TRAIN window."""
    lo, hi = pd.Timestamp(TRAIN[0], tz="UTC"), pd.Timestamp(TRAIN[1], tz="UTC")
    return df.loc[(df.index >= lo) & (df.index < hi)]


def oos_slice(df: pd.DataFrame | pd.Series, unlock: str | None = None):
    """Rows inside the OOS window — refuses to run without an explicit reason.

    Guard, not security: it makes touching out-of-sample data a deliberate act
    that shows up in the notebook. Pass ``unlock="<what rule is frozen>"``.
    """
    if not unlock:
        raise RuntimeError(
            "OOS is locked. Pass unlock='<which frozen rule you are testing>' "
            "and record it in README.md before looking at 2025-07..2026-04."
        )
    print(f"[OOS UNLOCKED] {unlock}")
    lo, hi = pd.Timestamp(OOS[0], tz="UTC"), pd.Timestamp(OOS[1], tz="UTC")
    return df.loc[(df.index >= lo) & (df.index < hi)]


# ---- cost model ------------------------------------------------------------
FEE_BPS = 5.5      # Bybit taker, per side, per leg (user gets rebates; ignored)
SLIP_BPS = 5.0     # flat slippage proxy, per side, per leg
SIDE_BPS = FEE_BPS + SLIP_BPS   # 10.5 bps to move 1 unit of notional once


def cost_bps(sides: int = 2) -> float:
    """Cost in bps **of gross notional traded**, for `sides` crossings.

    Note the subtlety that governs the whole basket hypothesis: fees are
    proportional to each leg's notional, so a 4-leg basket holding the same
    GROSS notional as a 2-leg pair pays the SAME total fee — cost scales with
    gross notional turned over, not with leg count. Multi-leg baskets get
    expensive only if (a) you size each leg like a full position, inflating
    gross, or (b) the basket spread is so quiet you need more gross to earn the
    same dollars. Both are measurable, not assumed.
    """
    return SIDE_BPS * sides


ROUND_TRIP_BPS = cost_bps(2)   # 21.0 bps of gross notional, in and out


# ---- universe --------------------------------------------------------------
def list_symbols() -> list[str]:
    """Every symbol with 1m klines on disk (no coverage filter)."""
    return sorted(p.name for p in loader.DATA_ROOT.iterdir() if p.is_dir())


def coverage(symbol: str) -> list[str]:
    """Sorted month stamps ('YYYY-MM') of parquet present for a symbol."""
    return sorted(p.stem for p in (loader.DATA_ROOT / symbol).glob("*.parquet"))


def universe(months: list[str] | None = None) -> list[str]:
    """Symbols with a COMPLETE set of monthly parquet over `months`.

    Defaults to the full research window. Survivorship caveat: these are the
    symbols still listed at the end of the window, so anything delisted mid-way
    is absent. Measured, not hand-waved, in nb00.
    """
    months = months or FULL_MONTHS
    want = set(months)
    return [s for s in list_symbols() if want <= set(coverage(s))]


# ---- price panel -----------------------------------------------------------
def panel_path(kind: str, tf: str) -> Path:
    return OUT / f"panel_{kind}_{tf}.parquet"


def load_panel(kind: str = "close", tf: str = "1h") -> pd.DataFrame:
    """Wide panel: rows = UTC timestamps, cols = symbols.

    ``kind`` is one of the panels written by ``_build_panel.py``
    ('close', 'dollarvol', 'high', 'low'). Build it first if missing.
    """
    p = panel_path(kind, tf)
    if not p.exists():
        raise FileNotFoundError(
            f"{p.name} not built yet. Run:  uv run python _build_panel.py"
        )
    return pd.read_parquet(p)


def resample_panel(px: pd.DataFrame, tf: str, how: str = "last") -> pd.DataFrame:
    """Coarsen an existing panel (e.g. 1h -> 4h/1d). 'last' for prices, 'sum' for volume."""
    return px.resample(tf).agg(how).dropna(how="all")


# ---- spread algebra (N legs; a pair is N=2) --------------------------------
def spread(px: pd.DataFrame, legs: list[str], w: np.ndarray) -> pd.Series:
    """Weighted log-price combination  sum_i w_i * log(P_i)  over `legs`.

    This is the object every stat-arb strategy trades, at any leg count. With
    ``legs=[a, b]`` and ``w=[1, -beta]`` it is the classic pair log-spread; with
    four legs it is a basket. Weights are in LOG space, i.e. they are notional
    proportions of each leg, not share counts.
    """
    lp = np.log(px[legs])
    return lp.mul(np.asarray(w, dtype=float), axis=1).sum(axis=1)


def funding_index(index: pd.DatetimeIndex, syms: list[str]) -> np.ndarray:
    """Cumulative funding paid by a LONG position, aligned to `index`.

    Shape (bars x symbols). Built as a cumulative sum so that the funding cost of
    holding a leg from bar a to bar b is just `CF[b] - CF[a]` — the same algebra
    as the price spread, which is what lets funding drop straight into the PnL:

        funding_pnl = -side * (dCF_y - beta * dCF_x) / gross

    Sign convention: a positive rate means longs pay shorts (the usual case on
    perps), hence the leading minus for a long-spread position.
    """
    CF = np.zeros((len(index), len(syms)))
    for si, s in enumerate(syms):
        f = loader.load_funding(s, index[0], index[-1] + pd.Timedelta(hours=1))
        if f.empty:
            continue
        pos = index.searchsorted(f.index, side="left")
        add = np.zeros(len(index) + 1)
        np.add.at(add, np.clip(pos, 0, len(index)), f.rate.values)
        CF[:, si] = np.cumsum(add[:-1])
    return CF


def gross(w: np.ndarray) -> float:
    """Gross notional per 1 unit of spread position = sum |w_i|.

    The number costs are charged on. A pair with w=[1,-1] has gross 2; an
    equal-weighted 4-leg basket normalised the same way has gross ~2 as well —
    which is exactly why 'more legs = more fees' needs proof, not assumption.
    """
    return float(np.abs(np.asarray(w, dtype=float)).sum())


__all__ = [
    "np", "pd", "plt", "loader", "REPO", "HERE", "OUT",
    "show",
    "FULL", "TRAIN", "OOS", "FULL_MONTHS", "train_slice", "oos_slice",
    "FEE_BPS", "SLIP_BPS", "SIDE_BPS", "cost_bps", "ROUND_TRIP_BPS",
    "list_symbols", "coverage", "universe",
    "panel_path", "load_panel", "resample_panel",
    "spread", "gross", "funding_index",
]
