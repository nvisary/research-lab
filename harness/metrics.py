"""Performance metrics from a portfolio equity curve."""
from __future__ import annotations

import numpy as np
import pandas as pd


# Crypto: 24/7, no holidays. periods_per_year = 365.25 * bars_per_day.
# Source-of-truth lookup; harness tools should ALWAYS pass tf when known so
# the factor doesn't depend on whether the data has gaps or partial coverage.
TF_PERIODS_PER_YEAR: dict[str, float] = {
    "1min":  365.25 * 24 * 60,    # 525_960
    "5min":  365.25 * 24 * 12,    # 105_192
    "15min": 365.25 * 24 * 4,     # 35_064
    "30min": 365.25 * 24 * 2,     # 17_532
    "1h":    365.25 * 24,         # 8_766
    "2h":    365.25 * 12,         # 4_383
    "4h":    365.25 * 6,          # 2_191.5
    "6h":    365.25 * 4,          # 1_461
    "8h":    365.25 * 3,          # 1_095.75
    "12h":   365.25 * 2,          # 730.5
    "1d":    365.25,              # 365.25
    "1w":    52.1429,
}


def _resolve_periods_per_year(index: pd.DatetimeIndex, tf: str | None) -> float:
    """Periods-per-year for annualizing Sharpe/Sortino.

    If ``tf`` is provided and known, return the canonical factor —
    independent of how many bars the sample actually contains. Sample
    size still affects the std-error of the estimator, but the *unit
    of measurement* is the natural year-rate of the bar.

    If ``tf`` is None or unknown, fall back to inferring from the index
    spacing (legacy behaviour). The fallback under-estimates the factor
    when data has gaps, which deflates the annualized Sharpe; it's
    correct when bars are perfectly contiguous.
    """
    if tf and tf in TF_PERIODS_PER_YEAR:
        return TF_PERIODS_PER_YEAR[tf]
    # accept '60min' / '1H' / '1Min' aliases via pandas
    if tf:
        try:
            secs = pd.Timedelta(tf).total_seconds()
            if secs > 0:
                return (365.25 * 24 * 3600) / secs
        except Exception:
            pass
    if len(index) < 2:
        return 1.0
    dt_seconds = (index[-1] - index[0]).total_seconds() / max(len(index) - 1, 1)
    if dt_seconds <= 0:
        return 1.0
    return (365.25 * 24 * 3600) / dt_seconds


# kept for backward-compat — call sites that haven't been threaded yet still work
def _annualization_factor(index: pd.DatetimeIndex, tf: str | None = None) -> float:
    return _resolve_periods_per_year(index, tf)


def sharpe(returns: pd.Series, tf: str | None = None) -> float:
    r = returns.dropna()
    if len(r) < 2 or r.std(ddof=0) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=0)
                 * np.sqrt(_resolve_periods_per_year(r.index, tf)))


def sortino(returns: pd.Series, tf: str | None = None) -> float:
    r = returns.dropna()
    downside = r[r < 0]
    if len(r) < 2 or downside.std(ddof=0) == 0 or len(downside) == 0:
        return 0.0
    return float(r.mean() / downside.std(ddof=0)
                 * np.sqrt(_resolve_periods_per_year(r.index, tf)))


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
            n_trades: int, tf: str | None = None,
            benchmark: pd.Series | None = None) -> dict:
    # PSR is computed inline; DSR (which needs n_trials) is added by the caller.
    from harness.stats import psr as _psr, bootstrap_sharpe_ci as _ci
    sh = sharpe(returns, tf=tf)
    psr_value = _psr(returns, tf=tf) if len(returns.dropna()) >= 30 else 0.0
    try:
        ci_lo, ci_hi = (_ci(returns, n_boot=400, tf=tf)
                        if len(returns.dropna()) >= 100 else (sh, sh))
    except Exception:
        ci_lo, ci_hi = sh, sh
    bench_sh: float | None = None
    if benchmark is not None and len(benchmark.dropna()) > 1:
        bench_sh = float(sharpe(benchmark.pct_change(), tf=tf))
    return {
        "sharpe": sh,
        "bench_sharpe": bench_sh,
        "alpha_sharpe": (sh - bench_sh) if bench_sh is not None else None,
        "sortino": sortino(returns, tf=tf),
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
    """OOS_Sharpe − λ·MaxDD with smooth low-activity penalty.

    Below ``min_trades`` we apply a graded penalty
        ``low_trades_penalty * (1 - sqrt(n / min_trades))``
    so a strategy with 49 trades is essentially unpenalized while a strategy
    with 5 trades pays roughly 2/3 of the full penalty. The previous version
    was a step function (-0.5 if n<50, 0 otherwise), which created a 0.5-point
    cliff at exactly 49 trades. n=0 remains ``-∞`` (ineligible).
    """
    import math
    sh = metrics.get("sharpe", 0.0)
    dd = metrics.get("max_dd", 0.0)
    n = metrics.get("n_trades", 0)
    if n == 0:
        return float("-inf")
    score = sh - dd_penalty * dd
    if n < min_trades:
        deficit = 1.0 - math.sqrt(n / min_trades)
        score -= low_trades_penalty * deficit
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
    cagrs = [m.get("cagr", 0.0) for m in window_metrics]
    total_returns = [m.get("total_return", 0.0) for m in window_metrics]
    bench_sharpes = [m.get("bench_sharpe") for m in window_metrics]
    alphas = [m.get("alpha_sharpe") for m in window_metrics]
    bench_sharpes_clean = [s for s in bench_sharpes if s is not None]
    alphas_clean = [a for a in alphas if a is not None]
    return score, {
        "mean_sharpe": float(np.mean(sharpes)),
        "std_sharpe": float(np.std(sharpes, ddof=0)),
        "median_sharpe": float(np.median(sharpes)),
        "mean_max_dd": float(np.mean(dds)),
        "worst_max_dd": float(np.max(dds)),
        "mean_n_trades": float(np.mean(trades)),
        "mean_cagr": float(np.mean(cagrs)),
        "median_cagr": float(np.median(cagrs)),
        "mean_total_return": float(np.mean(total_returns)),
        "mean_bench_sharpe": float(np.mean(bench_sharpes_clean)) if bench_sharpes_clean else None,
        "mean_alpha_sharpe": float(np.mean(alphas_clean)) if alphas_clean else None,
        "median_alpha_sharpe": float(np.median(alphas_clean)) if alphas_clean else None,
        "window_alphas": alphas,
        "n_windows": len(window_metrics),
        "window_composites": composites,
    }
