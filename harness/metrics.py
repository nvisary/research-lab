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
    return {
        "sharpe": sharpe(returns),
        "sortino": sortino(returns),
        "calmar": calmar(equity),
        "cagr": cagr(equity),
        "max_dd": max_drawdown(equity),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) else 0.0,
        "turnover": turnover(positions),
        "hit_rate": hit_rate(returns),
        "n_trades": int(n_trades),
        "n_periods": int(len(returns)),
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
        return float("-inf")  # ineligible: a strategy that never trades is not a strategy
    score = sh - dd_penalty * dd
    if n < min_trades:
        score -= low_trades_penalty
    return float(score)
