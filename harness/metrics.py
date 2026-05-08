"""Performance metrics from a portfolio equity curve."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _annualization_factor(index: pd.DatetimeIndex) -> float:
    """Estimate periods-per-year from the index spacing."""
    if len(index) < 2:
        return 1.0
    dt_seconds = (index[-1] - index[0]).total_seconds() / max(len(index) - 1, 1)
    return (365.25 * 24 * 3600) / dt_seconds


def sharpe(returns: pd.Series) -> float:
    r = returns.dropna()
    if len(r) < 2 or r.std(ddof=0) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=0) * np.sqrt(_annualization_factor(r.index)))


def sortino(returns: pd.Series) -> float:
    r = returns.dropna()
    downside = r[r < 0]
    if len(r) < 2 or downside.std(ddof=0) == 0 or len(downside) == 0:
        return 0.0
    return float(r.mean() / downside.std(ddof=0) * np.sqrt(_annualization_factor(r.index)))


def max_drawdown(equity: pd.Series) -> float:
    """Return max drawdown as a positive fraction (e.g. 0.23 == 23%)."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(-dd.min())


def cagr(equity: pd.Series) -> float:
    if equity.empty or equity.iloc[0] <= 0:
        return 0.0
    years = (equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 24 * 3600)
    if years <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0)


def calmar(equity: pd.Series) -> float:
    dd = max_drawdown(equity)
    if dd == 0:
        return 0.0
    return cagr(equity) / dd


def turnover(positions: pd.DataFrame) -> float:
    """Average daily turnover (sum of |Δposition| per day, averaged across days)."""
    if positions.empty:
        return 0.0
    dpos = positions.diff().abs().sum(axis=1)
    daily = dpos.resample("1D").sum()
    return float(daily.mean())


def hit_rate(returns: pd.Series) -> float:
    r = returns[returns != 0].dropna()
    if r.empty:
        return 0.0
    return float((r > 0).mean())


def summary(equity: pd.Series, returns: pd.Series, positions: pd.DataFrame,
            n_trades: int) -> dict:
    # PSR is computed inline; DSR (which needs n_trials) is added by the caller.
    from harness.stats import psr as _psr, bootstrap_sharpe_ci as _ci
    sh = sharpe(returns)
    psr_value = _psr(returns) if len(returns.dropna()) >= 30 else 0.0
    try:
        ci_lo, ci_hi = _ci(returns, n_boot=400) if len(returns.dropna()) >= 100 else (sh, sh)
    except Exception:
        ci_lo, ci_hi = sh, sh
    return {
        "sharpe": sh,
        "sortino": sortino(returns),
        "calmar": calmar(equity),
        "cagr": cagr(equity),
        "max_dd": max_drawdown(equity),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) else 0.0,
        "turnover": turnover(positions),
        "hit_rate": hit_rate(returns),
        "n_trades": int(n_trades),
        "n_periods": int(len(returns)),
        "psr": float(psr_value),
        "sharpe_ci_lo": float(ci_lo),
        "sharpe_ci_hi": float(ci_hi),
    }


def composite_score(metrics: dict, dd_penalty: float = 0.5,
                    min_trades: int = 50, low_trades_penalty: float = 0.5) -> float:
    """OOS_Sharpe − λ·MaxDD with low-activity penalty.

    Strategies with fewer than `min_trades` get a flat penalty so the agent
    cannot cheat by holding a single lucky position.
    """
    sh = metrics.get("sharpe", 0.0)
    dd = metrics.get("max_dd", 0.0)
    n = metrics.get("n_trades", 0)
    if n == 0:
        return float("-inf")
    score = sh - dd_penalty * dd
    if n < min_trades:
        score -= low_trades_penalty
    return float(score)


def aggregate_wf_composite(window_metrics: list[dict],
                           dd_penalty: float = 0.5,
                           min_trades: int = 50,
                           low_trades_penalty: float = 0.5,
                           stability_penalty: float = 0.5) -> tuple[float, dict]:
    """Aggregate a list of per-window OOS metric dicts into a single composite.

    score = mean(window_composites) − stability_penalty · std(window_composites)

    The standard-deviation term rewards strategies whose OOS Sharpe is consistent
    across windows over those whose mean Sharpe is the same but driven by one
    lucky window. ``-∞`` is returned if any window scored ``-∞`` (e.g. zero
    trades) — we don't want to average an unbounded-bad result.

    Returns
    -------
    (score, agg) where agg is a dict of summary stats: mean_sharpe, std_sharpe,
    median_sharpe, mean_max_dd, worst_max_dd, mean_n_trades, n_windows.
    """
    import numpy as np

    composites = [composite_score(m, dd_penalty, min_trades, low_trades_penalty)
                  for m in window_metrics]
    if not composites or any(c == float("-inf") for c in composites):
        return float("-inf"), {
            "mean_sharpe": 0.0, "std_sharpe": 0.0, "median_sharpe": 0.0,
            "mean_max_dd": 0.0, "worst_max_dd": 0.0,
            "mean_n_trades": 0.0, "n_windows": len(window_metrics),
        }

    mean_c = float(np.mean(composites))
    std_c = float(np.std(composites, ddof=0))
    score = mean_c - stability_penalty * std_c

    sharpes = [m.get("sharpe", 0.0) for m in window_metrics]
    dds = [m.get("max_dd", 0.0) for m in window_metrics]
    trades = [m.get("n_trades", 0) for m in window_metrics]
    return score, {
        "mean_sharpe": float(np.mean(sharpes)),
        "std_sharpe": float(np.std(sharpes, ddof=0)),
        "median_sharpe": float(np.median(sharpes)),
        "mean_max_dd": float(np.mean(dds)),
        "worst_max_dd": float(np.max(dds)),
        "mean_n_trades": float(np.mean(trades)),
        "n_windows": len(window_metrics),
        "window_composites": composites,
    }
