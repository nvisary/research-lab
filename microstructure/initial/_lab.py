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


# ---- SEEING microstructure: the liquidity heatmap --------------------------
import matplotlib.colors as mcolors  # noqa: E402


def _levels(df: pd.DataFrame, side: str, kind: str, n: int = 25):
    return [f"{side}_{kind}_{i}" for i in range(n) if f"{side}_{kind}_{i}" in df.columns]


def _liquidity_grid(ob: pd.DataFrame, side: str, xs, price_edges):
    """Scatter-add resting size into a [n_price, n_time] grid.

    ``xs`` = per-row x (seconds from window start). ``price_edges`` = the price
    bin boundaries. Returns the grid (mean size per cell), NaN where empty.
    """
    px = ob[_levels(ob, side, "px")].to_numpy()
    sz = ob[_levels(ob, side, "sz")].to_numpy()
    nlv = px.shape[1]
    nx = len(xs) if np.ndim(xs) else 0
    # column index per row (0..NX-1), broadcast to every level
    NX = _liquidity_grid.NX
    NY = len(price_edges) - 1
    col = np.clip(((xs - xs[0]) / (xs[-1] - xs[0] + 1e-9) * NX).astype(int), 0, NX - 1)
    col = np.repeat(col, nlv)
    row = np.clip(np.searchsorted(price_edges, px.ravel(), side="right") - 1, 0, NY - 1)
    val = sz.ravel()
    ok = np.isfinite(val) & (val > 0)
    acc = np.zeros((NY, NX)); cnt = np.zeros((NY, NX))
    np.add.at(acc, (row[ok], col[ok]), val[ok])
    np.add.at(cnt, (row[ok], col[ok]), 1.0)
    grid = np.divide(acc, cnt, out=np.full_like(acc, np.nan), where=cnt > 0)
    return grid


def micro_view(session, sym, t0=None, t1=None, NX=700, NY=280,
               trade_top_pct=70, title=None, name=None):
    """**The picture**: order-book liquidity heatmap + trade tape + CVD + force.

    Renders a recorded microstructure window the way a trader *sees* order flow:

    - **Heatmap** — x=time, y=price, colour=resting size. Bids (support) below in
      blue, asks (resistance) above in warm. Bright horizontal bands = walls;
      watch them appear (refill) or vanish (pull/spoof). Log-scaled (sizes are
      heavy-tailed) so a wall stands out without one whale washing the rest out.
    - **Mid price** — white line weaving through the book.
    - **Trades** — bubbles on the price panel: green=taker buy, red=taker sell,
      size ∝ amount (only the top ``trade_top_pct`` pct by size, else it's mush).
    - **CVD** — cumulative signed taker volume (net aggression).
    - **Force** — taker volume/sec (how hard the tape is hitting), buy vs sell.

    Pass ``t0``/``t1`` (parseable timestamps) to zoom. Saves ``_out/<name>.png``.
    """
    ob = load(session, "orderbook", sym, with_datetime=True)
    tr = load(session, "trades", sym, with_datetime=True)
    if t0 is not None or t1 is not None:
        ob = ob.loc[t0:t1]; tr = tr.loc[t0:t1]
    if len(ob) < 2:
        raise ValueError("window too small / empty")

    start = ob.index[0]
    xs = (ob.index - start).total_seconds().to_numpy()
    tr_x = (tr.index - start).total_seconds().to_numpy()
    m = mid(ob)

    # price range from the visible book, small pad
    lo = ob[_levels(ob, "bid", "px")].min().min()
    hi = ob[_levels(ob, "ask", "px")].max().max()
    price_edges = np.linspace(lo, hi, NY + 1)
    _liquidity_grid.NX = NX
    bid_g = _liquidity_grid(ob, "bid", xs, price_edges)
    ask_g = _liquidity_grid(ob, "ask", xs, price_edges)

    def _norm(g):
        v = g[np.isfinite(g)]
        if v.size == 0:
            return mcolors.LogNorm(1, 10)
        return mcolors.LogNorm(max(np.nanpercentile(v, 70), 1e-9), np.nanpercentile(v, 99.7))

    extent = [0, xs[-1], lo, hi]
    fig, (ax, axc, axf) = plt.subplots(
        3, 1, figsize=(15, 10), sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1, 1]})
    ax.set_facecolor("#0b0b12")
    ax.imshow(ask_g, origin="lower", extent=extent, aspect="auto",
              cmap="inferno", norm=_norm(ask_g), interpolation="nearest")
    ax.imshow(bid_g, origin="lower", extent=extent, aspect="auto",
              cmap="Blues", norm=_norm(bid_g), interpolation="nearest", alpha=0.85)
    ax.plot(xs, m.to_numpy(), color="white", lw=1.0, alpha=0.9)

    # trades as bubbles (top pct by size only; auto-cap to ~3000 so it isn't mush)
    if len(tr):
        amt = tr["amount"].to_numpy()
        pct = max(trade_top_pct, 100 * (1 - 3000 / len(amt))) if len(amt) > 3000 else trade_top_pct
        thr = np.percentile(amt, pct)
        keep = amt >= thr
        buy = keep & (tr["side"].to_numpy() == "buy")
        sell = keep & (tr["side"].to_numpy() == "sell")
        ss = 6 + 90 * (amt - thr) / (amt.max() - thr + 1e-9)
        ax.scatter(tr_x[buy], tr["price"].to_numpy()[buy], s=ss[buy],
                   c="#25ff9a", alpha=0.5, edgecolors="none", zorder=3)
        ax.scatter(tr_x[sell], tr["price"].to_numpy()[sell], s=ss[sell],
                   c="#ff4d5e", alpha=0.5, edgecolors="none", zorder=3)
    ax.set_ylabel("price"); ax.set_ylim(lo, hi)
    ax.set_title(title or f"{sym} microstructure — {session}", color="#ddd")

    # CVD
    sgn = np.where(tr["side"] == "buy", 1.0, -1.0)
    cvd = pd.Series(sgn * tr["amount"].to_numpy(), index=tr.index).cumsum()
    axc.plot((cvd.index - start).total_seconds(), cvd.to_numpy(), color="#f0c000", lw=1)
    axc.axhline(0, color="#666", lw=0.6, ls="--"); axc.set_ylabel("CVD")

    # force = taker vol/sec, buy vs sell, 5s bins
    buy_v = tr.loc[tr["side"] == "buy", "amount"].resample("5s").sum()
    sell_v = tr.loc[tr["side"] == "sell", "amount"].resample("5s").sum()
    bx = (buy_v.index - start).total_seconds()
    axf.fill_between(bx, 0, buy_v.to_numpy() / 5, color="#25ff9a", alpha=0.6, step="mid")
    axf.fill_between((sell_v.index - start).total_seconds(), 0,
                     -sell_v.to_numpy() / 5, color="#ff4d5e", alpha=0.6, step="mid")
    axf.axhline(0, color="#666", lw=0.6); axf.set_ylabel("force\n(vol/s)")
    axf.set_xlabel("seconds from window start"); axf.set_xlim(0, xs[-1])
    return show(name or f"micro_{sym}")
