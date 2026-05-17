"""Combinatorial Purged Cross-Validation paths and evaluation.

CPCV (López de Prado, *Advances in Financial Machine Learning*, ch. 12)
splits the dataset into ``n_groups`` consecutive groups of equal time
length, then iterates over every C(n_groups, k_test) combination as
"test groups". Train = the rest of the groups. Combined with a
forward **embargo** that drops bars immediately after each test group,
this yields a much larger ensemble of test/train splits than
plain walk-forward — and thus a much tighter distribution on the
strategy's OOS Sharpe.

Why no "purge"
--------------
Purge removes from train the samples whose **label horizon** overlaps
test samples. Our strategies emit ``position[t]`` with a one-bar
shift (no horizon to leak), so there's nothing to purge in the
labeling sense. We implement only the **embargo** half — drop the
first ``embargo`` worth of bars after each test block — which
addresses serial correlation between the end of test and the start of
the next train segment.

Cheap evaluation
----------------
In our framework the strategy is fixed code, not a fitted model:
``generate_signals(data, params)`` is deterministic given inputs.
That means the SAME equity curve answers every CPCV path — we don't
need 45 separate backtests. We run the backtest **once** over the
full period, then for each path mask the returns by the union of
OOS intervals and re-aggregate metrics. Cost ≈ 1 walk-forward
backtest, not C(n_groups, k_test).

This is unusual relative to ML-style CPCV where each path requires a
fresh model fit; it is correct here because there is no fitting
inside the harness — the LLM-agent loop is the optimizer.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from harness.metrics import (
    _resolve_periods_per_year,
    cagr,
    hit_rate,
    max_drawdown,
    sortino,
)


Interval = tuple[pd.Timestamp, pd.Timestamp]


@dataclass(frozen=True)
class CPCVPath:
    """One CPCV combination.

    ``test_groups`` is the tuple of group indices that form OOS.
    ``train_intervals`` and ``oos_intervals`` are sorted, non-overlapping
    [start, end) Timestamp pairs covering the train and OOS bars
    respectively (after embargo). Their union plus embargo dropouts
    equals the full period.
    """
    test_groups: tuple[int, ...]
    train_intervals: tuple[Interval, ...]
    oos_intervals: tuple[Interval, ...]


# --------------------------------------------------------------------------- #
# Path generation
# --------------------------------------------------------------------------- #
def _subtract_zones(start: pd.Timestamp, end: pd.Timestamp,
                    zones: Iterable[Interval]) -> list[Interval]:
    """Return [start, end) minus any overlap with `zones` (a list of [a,b))."""
    segments = [(start, end)]
    for z_start, z_end in zones:
        new_segments: list[Interval] = []
        for s, e in segments:
            if z_end <= s or z_start >= e:
                new_segments.append((s, e))     # disjoint
                continue
            if z_start > s:
                new_segments.append((s, min(e, z_start)))
            if z_end < e:
                new_segments.append((max(s, z_end), e))
        segments = new_segments
    return [(s, e) for s, e in segments if e > s]


def _merge_adjacent(intervals: list[Interval]) -> list[Interval]:
    """Merge intervals that share a boundary (or overlap)."""
    if not intervals:
        return []
    sorted_iv = sorted(intervals, key=lambda iv: iv[0])
    out: list[Interval] = [sorted_iv[0]]
    for s, e in sorted_iv[1:]:
        last_s, last_e = out[-1]
        if s <= last_e:
            out[-1] = (last_s, max(last_e, e))
        else:
            out.append((s, e))
    return out


def cpcv_paths(period_start: str | pd.Timestamp, period_end: str | pd.Timestamp,
               n_groups: int = 10, k_test: int = 2,
               embargo: pd.Timedelta | str | None = None) -> list[CPCVPath]:
    """Generate every CPCV path for the given (n_groups, k_test, embargo).

    Number of paths = C(n_groups, k_test). For (10, 2) → 45 paths;
    (8, 2) → 28; (12, 3) → 220 (each adds CPU; 45 is a sweet spot).

    Embargo applies forward only: after each test group, the next
    ``embargo`` worth of bars is dropped from train (and never appears
    in OOS — they are ALWAYS in test for some path, but only counted
    in OOS for those paths).
    """
    s = pd.Timestamp(period_start)
    e = pd.Timestamp(period_end)
    s = s.tz_convert("UTC") if s.tzinfo else s.tz_localize("UTC")
    e = e.tz_convert("UTC") if e.tzinfo else e.tz_localize("UTC")
    if n_groups < 2:
        raise ValueError(f"n_groups must be >= 2 (got {n_groups})")
    if k_test < 1 or k_test >= n_groups:
        raise ValueError(f"k_test must be in [1, n_groups-1] (got {k_test} of {n_groups})")
    embargo_td = pd.Timedelta(embargo) if embargo else pd.Timedelta(0)

    group_len = (e - s) / n_groups
    boundaries = [s + group_len * i for i in range(n_groups + 1)]

    paths: list[CPCVPath] = []
    for combo in itertools.combinations(range(n_groups), k_test):
        # Embargo zones: one per test group, just after its end.
        embargo_zones: list[Interval] = []
        for g in combo:
            z_start = boundaries[g + 1]
            z_end = min(z_start + embargo_td, e)
            if z_end > z_start:
                embargo_zones.append((z_start, z_end))

        oos_segs: list[Interval] = []
        train_segs: list[Interval] = []
        for i in range(n_groups):
            iv = (boundaries[i], boundaries[i + 1])
            if i in combo:
                oos_segs.append(iv)
            else:
                train_segs.extend(_subtract_zones(iv[0], iv[1], embargo_zones))

        paths.append(CPCVPath(
            test_groups=tuple(combo),
            train_intervals=tuple(_merge_adjacent(train_segs)),
            oos_intervals=tuple(_merge_adjacent(oos_segs)),
        ))
    return paths


# --------------------------------------------------------------------------- #
# Path evaluation
# --------------------------------------------------------------------------- #
def _intervals_mask(index: pd.DatetimeIndex,
                    intervals: Iterable[Interval]) -> np.ndarray:
    """Boolean mask: True for bars within ANY of the [start, end) intervals."""
    mask = np.zeros(len(index), dtype=bool)
    for s, e in intervals:
        mask |= (index >= s) & (index < e)
    return mask


def _sharpe_on_concat(returns: pd.Series, tf: str | None) -> float:
    """Sharpe on a (possibly non-contiguous) returns series.

    The sample returns are concatenated in chronological order and
    treated as if contiguous; annualization uses the TF factor (so
    each bar represents the same timespan regardless of gaps).
    """
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    if sd == 0:
        return 0.0
    return float(r.mean() / sd * math.sqrt(_resolve_periods_per_year(r.index, tf)))


def evaluate_path(returns: pd.Series, equity: pd.Series,
                  trades: pd.DataFrame | None,
                  path: CPCVPath, tf: str | None) -> dict:
    """Compute OOS metrics for a single CPCV path.

    The returns and equity series should cover the full period. The
    path's ``oos_intervals`` define the slice to score.
    """
    if returns.empty:
        return {"test_groups": path.test_groups,
                "n_periods": 0, "n_trades": 0,
                "sharpe": 0.0, "sortino": 0.0, "max_dd": 0.0,
                "total_return": 0.0, "hit_rate": 0.0, "cagr": 0.0}

    oos_mask = _intervals_mask(returns.index, path.oos_intervals)
    rets_oos = returns[oos_mask]
    if rets_oos.empty:
        return {"test_groups": path.test_groups,
                "n_periods": 0, "n_trades": 0,
                "sharpe": 0.0, "sortino": 0.0, "max_dd": 0.0,
                "total_return": 0.0, "hit_rate": 0.0, "cagr": 0.0}

    init = float(equity.iloc[0]) if len(equity) else 1.0
    equity_oos = (1.0 + rets_oos).cumprod() * init

    n_trades_oos = 0
    if trades is not None and not trades.empty and "entry_time" in trades.columns:
        trade_mask = _intervals_mask(
            pd.DatetimeIndex(trades["entry_time"].values, tz="UTC"),
            path.oos_intervals,
        )
        n_trades_oos = int(trade_mask.sum())

    # In-sample (train-side) Sharpe on the path. Needed by PBO/overfit
    # diagnostics (harness.pbo.cpcv_overfit_stats) which compares the
    # IS vs OOS rank of each path. Cheap — just mask & one Sharpe call.
    is_mask = _intervals_mask(returns.index, path.train_intervals)
    rets_is = returns[is_mask]
    is_sharpe = _sharpe_on_concat(rets_is, tf) if not rets_is.empty else 0.0

    return {
        "test_groups": path.test_groups,
        "n_periods": int(len(rets_oos)),
        "n_periods_is": int(len(rets_is)),
        "n_trades": n_trades_oos,
        "sharpe": _sharpe_on_concat(rets_oos, tf),
        "is_sharpe": is_sharpe,
        "sortino": float(sortino(rets_oos, tf=tf)),
        "max_dd": float(max_drawdown(equity_oos)),
        "total_return": float(equity_oos.iloc[-1] / equity_oos.iloc[0] - 1.0),
        "hit_rate": float(hit_rate(rets_oos)),
        # CAGR on a non-contiguous concatenation isn't well-defined in
        # calendar time; leave NaN to avoid misinterpretation.
        "cagr": float("nan"),
    }


def summarize_paths(path_results: list[dict]) -> dict:
    """Aggregate stats across CPCV paths."""
    if not path_results:
        return {"n_paths": 0}
    sharpes = np.array([p["sharpe"] for p in path_results], dtype=float)
    sortinos = np.array([p["sortino"] for p in path_results], dtype=float)
    dds = np.array([p["max_dd"] for p in path_results], dtype=float)
    trs = np.array([p["total_return"] for p in path_results], dtype=float)
    n_tr = np.array([p["n_trades"] for p in path_results], dtype=float)
    n_paths = len(sharpes)
    return {
        "n_paths": n_paths,
        "median_sharpe": float(np.median(sharpes)),
        "mean_sharpe": float(np.mean(sharpes)),
        "std_sharpe": float(np.std(sharpes, ddof=1)) if len(sharpes) >= 2 else 0.0,
        "iqr_sharpe": [float(np.quantile(sharpes, 0.25)),
                       float(np.quantile(sharpes, 0.75))],
        "p05_sharpe": float(np.quantile(sharpes, 0.05)),
        "p95_sharpe": float(np.quantile(sharpes, 0.95)),
        "pct_positive_sharpe": float((sharpes > 0).mean() * 100.0),
        "pct_above_1": float((sharpes > 1.0).mean() * 100.0),
        "median_sortino": float(np.median(sortinos)),
        "median_max_dd": float(np.median(dds)),
        "worst_max_dd": float(np.max(dds)),
        "median_total_return": float(np.median(trs)),
        "median_n_trades": float(np.median(n_tr)),
        "min_n_trades": int(np.min(n_tr)),
    }
