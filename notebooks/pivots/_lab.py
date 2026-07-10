"""Shared helpers for the daily-pivot-points research line.

Thin convenience layer on top of the project's canonical ``datafeed.loader``
(same contract as notebooks/pump/_lab.py, notebooks/rsi/_lab.py). Import at the
top of a notebook (lives in notebooks/pivots/) with::

    import sys; sys.path.insert(0, ".")   # cwd = notebooks/pivots
    from _lab import *

Train-friendly: wraps the loader, adds plotting defaults, and the two
indicators this line is built on (standard daily pivot points + CCI). It does
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

def daily_pivots(df: pd.DataFrame) -> pd.DataFrame:
    """Standard (floor-trader) daily pivot points from the PREVIOUS day's OHLC.

    Returns a frame aligned to ``df.index`` with columns ``p, s1, r1, s2, r2``.

        P  = (H + L + C) / 3        (the "pivot" / fair-value line)
        R1 = 2P - L    S1 = 2P - H
        R2 = P + (H-L) S2 = P - (H-L)

    where H/L/C are YESTERDAY's high/low/close. Fully causal: the levels for a
    given UTC day are computed from the prior day's completed candle and held
    flat all day, so at any intraday bar they are already known (no lookahead).
    This is exactly what ``strategies/pivot_cci`` uses.
    """
    daily = df.resample("D").agg({"high": "max", "low": "min", "close": "last"})
    prev = daily.shift(1)
    p = (prev["high"] + prev["low"] + prev["close"]) / 3
    out = pd.DataFrame({
        "p": p,
        "s1": 2 * p - prev["high"],
        "r1": 2 * p - prev["low"],
        "s2": p - (prev["high"] - prev["low"]),
        "r2": p + (prev["high"] - prev["low"]),
    })
    return out.reindex(df.index, method="ffill")


def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index on the typical price (H+L+C)/3.

    CCI = (TP - SMA(TP)) / (0.015 * mean_abs_dev(TP)). Oscillates roughly in
    +/-100; >100 = "overbought" / stretched above the mean, <-100 = "oversold".
    Causal (rolling window ending at bar t).
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * mad)


# ---- the real pivot_cci strategy (imported, not reimplemented) -------------
# We load the ACTUAL harness strategy code so the baseline is faithful. It
# exposes _signals_for_symbol(df, params, symbol) -> position series already
# .shift(1)'d (decided at bar close, executed next bar) and DEFAULT_PARAMS.
import importlib.util as _ilu  # noqa: E402

_STRAT_PATH = REPO / "strategies" / "pivot_cci" / "strategy.py"
_spec = _ilu.spec_from_file_location("pivot_cci_strategy", _STRAT_PATH)
pivot_cci = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(pivot_cci)
PCCI_PARAMS = dict(pivot_cci.DEFAULT_PARAMS)


def strat_signals(df: pd.DataFrame, symbol: str, params: dict | None = None) -> pd.Series:
    """Position series (+1/0/-1, already shift(1)) from the real pivot_cci code."""
    return pivot_cci._signals_for_symbol(df, params or PCCI_PARAMS, symbol)


ABL_ALL_ON = {"cci_level": True, "cci_turn": True, "rsi": True, "ema": True, "funding": True}


def strat_signals_ablated(df: pd.DataFrame, symbol: str, on: dict | None = None,
                          params: dict | None = None) -> pd.Series:
    """Faithful copy of pivot_cci._signals_for_symbol with a per-slice on/off
    switch (verified bit-for-bit vs the real code with ``on=ABL_ALL_ON`` in
    nb04). When a flag is False that entry filter is replaced by an all-True
    mask. Core (pivot touch + P/CCI-recovery/flip exit) is always active.

    Slices: ``cci_level`` (CCI<-thr), ``cci_turn`` (CCI turning), ``rsi``
    (RSI<thr), ``ema`` (EMA200 trend gate), ``funding`` (funding gate).
    """
    p = params or PCCI_PARAMS
    on = {**ABL_ALL_ON, **(on or {})}
    close, high, low = df["close"], df["high"], df["low"]
    cci_threshold = float(p["cci_threshold"]); rsi_threshold = float(p["rsi_threshold"])
    funding_threshold = float(p["funding_threshold"]); trend_period = int(p["trend_period"])
    cci_exit = float(p["cci_exit"]); cci_period = int(p["cci_period"]); rsi_period = int(p["rsi_period"])

    tp = (high + low + close) / 3
    ma_tp = tp.rolling(cci_period).mean()
    mean_dev = tp.rolling(cci_period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    cci_ = (tp - ma_tp) / (0.015 * mean_dev)

    delta = close.diff(); gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
    ag = gain.ewm(com=rsi_period - 1, adjust=False).mean(); al = loss.ewm(com=rsi_period - 1, adjust=False).mean()
    rsi_ = (100 - 100 / (1 + ag / al.replace(0, np.nan))).fillna(50)

    try:
        fund = loader.load_funding(symbol, df.index[0], df.index[-1]).reindex(df.index, method="ffill").fillna(0)["rate"]
    except Exception:
        fund = pd.Series(0.0, index=df.index)

    ema = close.ewm(span=trend_period, adjust=False).mean()
    T = pd.Series(True, index=df.index)

    daily = df.resample("D").agg({"high": "max", "low": "min", "close": "last"}).shift(1)
    p_ = (daily["high"] + daily["low"] + daily["close"]) / 3
    s1 = (2 * p_ - daily["high"]).reindex(df.index, method="ffill")
    r1 = (2 * p_ - daily["low"]).reindex(df.index, method="ffill")
    p_ = p_.reindex(df.index, method="ffill")

    long_cond = ((low < s1)
        & ((cci_ < -cci_threshold) if on["cci_level"] else T)
        & ((rsi_ < rsi_threshold) if on["rsi"] else T)
        & ((cci_ > cci_.shift(1)) if on["cci_turn"] else T)
        & ((close > ema) if on["ema"] else T)
        & ((fund < funding_threshold) if on["funding"] else T))
    short_cond = ((high > r1)
        & ((cci_ > cci_threshold) if on["cci_level"] else T)
        & ((rsi_ > (100 - rsi_threshold)) if on["rsi"] else T)
        & ((cci_ < cci_.shift(1)) if on["cci_turn"] else T)
        & ((close < ema) if on["ema"] else T)
        & ((fund > -funding_threshold) if on["funding"] else T))

    cci_np, p_np, close_np = cci_.to_numpy(), p_.to_numpy(), close.to_numpy()
    lc, sc = long_cond.to_numpy(), short_cond.to_numpy()
    cur = 0.0; out = np.zeros(len(df))
    for i in range(len(df)):
        if cur == 0:
            if lc[i]: cur = 1.0
            elif sc[i]: cur = -1.0
        elif cur == 1.0:
            if close_np[i] >= p_np[i] or cci_np[i] >= -cci_exit or sc[i]:
                cur = -1.0 if sc[i] else 0.0
        elif cur == -1.0:
            if close_np[i] <= p_np[i] or cci_np[i] <= cci_exit or lc[i]:
                cur = 1.0 if lc[i] else 0.0
        out[i] = cur
    return pd.Series(out, index=df.index).shift(1).fillna(0.0)


# ---- a small, transparent backtest engine ----------------------------------
BARS_PER_YEAR_1H = 24 * 365  # 8760


def backtest(pos: pd.Series, close: pd.Series, cost_side: float = 0.00075,
             bars_per_year: int = BARS_PER_YEAR_1H) -> dict:
    """Honest single-instrument backtest of a held-position series.

    ``pos`` is the position held DURING each bar (the strategy already shifted
    it). PnL for bar t = pos[t] * pct_change(close)[t]; a transaction cost of
    ``cost_side`` is charged on every unit of position change (so a full
    round-trip = 2*cost_side = 0.15% by default). Returns metrics + the equity
    and per-trade tables. NOT the harness (no WF split / DSR / activity penalty
    / funding-adjusted equity) — a descriptive full-period sim.
    """
    pos = pos.reindex(close.index).fillna(0.0)
    ret = close.pct_change().fillna(0.0)
    gross = pos * ret
    turnover = pos.diff().abs().fillna(pos.abs())
    net = gross - turnover * cost_side
    eq = (1.0 + net).cumprod()
    dd = eq / eq.cummax() - 1.0

    # per-trade segmentation: contiguous same-sign runs of pos
    trades = []
    p = pos.to_numpy()
    n = len(p)
    i = 0
    while i < n:
        if p[i] == 0:
            i += 1
            continue
        j = i
        while j + 1 < n and p[j + 1] == p[i]:
            j += 1
        seg = net.iloc[i:j + 1]
        trades.append({
            "entry": close.index[i], "exit": close.index[j],
            "dir": int(p[i]), "bars": j - i + 1,
            "pnl": float((1.0 + seg).prod() - 1.0),
        })
        i = j + 1
    tr = pd.DataFrame(trades)

    wins = tr[tr.pnl > 0].pnl if len(tr) else pd.Series(dtype=float)
    losses = tr[tr.pnl <= 0].pnl if len(tr) else pd.Series(dtype=float)
    pf = (wins.sum() / -losses.sum()) if len(losses) and losses.sum() != 0 else np.inf
    sharpe = (net.mean() / net.std() * np.sqrt(bars_per_year)) if net.std() > 0 else 0.0

    return {
        "equity": eq, "dd": dd, "net": net, "trades": tr,
        "total_return": float(eq.iloc[-1] - 1.0),
        "sharpe": float(sharpe),
        "max_dd": float(dd.min()),
        "n_trades": int(len(tr)),
        "win_rate": float((tr.pnl > 0).mean()) if len(tr) else 0.0,
        "avg_pnl": float(tr.pnl.mean()) if len(tr) else 0.0,
        "profit_factor": float(pf),
        "avg_hold": float(tr.bars.mean()) if len(tr) else 0.0,
        "time_in_pos": float((pos != 0).mean()),
        "n_long": int((tr.dir == 1).sum()) if len(tr) else 0,
        "n_short": int((tr.dir == -1).sum()) if len(tr) else 0,
    }


__all__ = [
    "np", "pd", "plt", "loader",
    "show", "ohlcv", "funding", "list_symbols", "coverage",
    "daily_pivots", "cci",
    "pivot_cci", "PCCI_PARAMS", "strat_signals", "strat_signals_ablated",
    "ABL_ALL_ON", "backtest", "BARS_PER_YEAR_1H",
]
