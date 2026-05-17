"""Drift detection for forward-test (post-holdout) strategy runs.

After a strategy has gone through iteration → holdout, its real-world
edge is whatever it produces on **bars it has never seen and that the
operator has never used to tune anything**. That's the forward window:
[holdout_end, now). Holding the same code constant and watching it
trade fresh bars is the closest proxy to live trading without an
execution layer.

This module is the diagnostic half — given:
  - a forward equity curve (computed by runner.forward),
  - the backtest's OOS Sharpe confidence interval (from best.json),
it answers:
  - is the realised forward Sharpe inside the backtest's CI?
  - is the trailing-30d Sharpe drifting consistently below CI lower?
  - is the forward sample large enough for PSR to mean anything?

Outputs a compact verdict the UI can color (ok / yellow / red) and
trajectory series for plotting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from harness.metrics import _resolve_periods_per_year


DriftFlag = Literal["ok", "warn", "alert", "unknown"]


# --------------------------------------------------------------------------- #
@dataclass
class DriftReport:
    """Bundle of drift-related stats for a single forward window."""
    n_periods: int
    forward_sharpe: float
    forward_total_return: float
    forward_max_dd: float
    forward_volatility: float
    backtest_sharpe: float | None
    backtest_sharpe_ci_lo: float | None
    backtest_sharpe_ci_hi: float | None
    sharpe_z_vs_backtest: float | None        # (forward - backtest_mid) / ci_half_width
    in_ci: bool | None
    consecutive_below_ci_days: int             # rolling-30d Sharpe below CI lower
    forward_psr: float | None                  # PSR of forward Sharpe against 0
    flag: DriftFlag
    flag_reason: str

    def to_dict(self) -> dict:
        return {
            "n_periods": self.n_periods,
            "forward_sharpe": self.forward_sharpe,
            "forward_total_return": self.forward_total_return,
            "forward_max_dd": self.forward_max_dd,
            "forward_volatility": self.forward_volatility,
            "backtest_sharpe": self.backtest_sharpe,
            "backtest_sharpe_ci_lo": self.backtest_sharpe_ci_lo,
            "backtest_sharpe_ci_hi": self.backtest_sharpe_ci_hi,
            "sharpe_z_vs_backtest": self.sharpe_z_vs_backtest,
            "in_ci": self.in_ci,
            "consecutive_below_ci_days": self.consecutive_below_ci_days,
            "forward_psr": self.forward_psr,
            "flag": self.flag,
            "flag_reason": self.flag_reason,
        }


# --------------------------------------------------------------------------- #
def _sharpe_ann(returns: np.ndarray, ann: float) -> float:
    if len(returns) < 2:
        return 0.0
    sd = returns.std(ddof=1)
    if sd <= 0:
        return 0.0
    return float(returns.mean() / sd * ann)


def _max_dd_magnitude(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(-dd.min())


def _psr(sharpe_ann: float, returns: np.ndarray, ann: float,
         benchmark_sharpe: float = 0.0) -> float | None:
    """Probabilistic Sharpe Ratio: P(true Sharpe > benchmark | observed,
    given sample size, skew, kurt). Bailey & López de Prado 2012.

    Returns None if the sample is too small (< 30) or degenerate.
    """
    from scipy.stats import norm as _norm
    n = len(returns)
    if n < 30:
        return None
    sd = returns.std(ddof=1)
    if sd <= 0:
        return None
    sharpe_pp = sharpe_ann / ann if ann > 0 else 0.0
    # Sample skew and excess kurtosis on per-period returns.
    centered = returns - returns.mean()
    m2 = (centered ** 2).mean()
    if m2 <= 0:
        return None
    skew = float(((centered ** 3).mean()) / (m2 ** 1.5))
    kurt = float(((centered ** 4).mean()) / (m2 ** 2)) - 3.0
    denom_sq = 1.0 - skew * sharpe_pp + (kurt / 4.0) * (sharpe_pp ** 2)
    if denom_sq <= 0:
        return None
    bench_pp = benchmark_sharpe / ann if ann > 0 else 0.0
    z = ((sharpe_pp - bench_pp) * np.sqrt(n - 1)) / np.sqrt(denom_sq)
    return float(_norm.cdf(z))


# --------------------------------------------------------------------------- #
def rolling_window_sharpe(returns: pd.Series, window_days: int = 30,
                           tf: str | None = None) -> pd.Series:
    """Trailing Sharpe over `window_days` days of returns.

    Works on any TF — the window is converted to a bar count using the
    index's average bar spacing. NaN where the window isn't full.
    """
    r = returns.dropna()
    if r.empty:
        return pd.Series(dtype="float64", name="rolling_sharpe")
    ann = float(np.sqrt(_resolve_periods_per_year(r.index, tf)))
    # Bars per day from index spacing.
    if len(r) < 2:
        return pd.Series(dtype="float64", name="rolling_sharpe")
    bar_seconds = (r.index[1] - r.index[0]).total_seconds()
    bars_per_day = max(1, int(round(86400.0 / bar_seconds)))
    win_bars = max(2, window_days * bars_per_day)

    def _w(x):
        x = x[~np.isnan(x)]
        if len(x) < 2:
            return np.nan
        sd = x.std(ddof=1)
        if sd <= 0:
            return np.nan
        return float(x.mean() / sd * ann)

    return r.rolling(win_bars, min_periods=win_bars).apply(_w, raw=True) \
        .rename("rolling_sharpe")


# --------------------------------------------------------------------------- #
def assess_drift(
    forward_returns: pd.Series,
    backtest_oos_sharpe: float | None,
    backtest_sharpe_ci_lo: float | None,
    backtest_sharpe_ci_hi: float | None,
    tf: str | None = None,
    consecutive_threshold_days: int = 14,
) -> DriftReport:
    """Compute the drift verdict given forward returns and the backtest's
    Sharpe CI.

    Flag logic:
      - **unknown** : forward sample too small (<10 bars).
      - **alert**   : (a) forward Sharpe well below CI lower (z < -1.5),
                      OR (b) rolling-30d Sharpe has been below CI lower
                      for ≥ ``consecutive_threshold_days`` consecutive days.
      - **warn**    : forward Sharpe below CI lower but z ≥ -1.5
                      OR consecutive below-CI streak is 7..(threshold-1) days.
      - **ok**      : forward Sharpe inside CI (or above).
    """
    r = forward_returns.dropna()
    n = len(r)
    if n < 10:
        return DriftReport(
            n_periods=int(n),
            forward_sharpe=0.0, forward_total_return=0.0,
            forward_max_dd=0.0, forward_volatility=0.0,
            backtest_sharpe=backtest_oos_sharpe,
            backtest_sharpe_ci_lo=backtest_sharpe_ci_lo,
            backtest_sharpe_ci_hi=backtest_sharpe_ci_hi,
            sharpe_z_vs_backtest=None,
            in_ci=None,
            consecutive_below_ci_days=0,
            forward_psr=None,
            flag="unknown",
            flag_reason=f"forward sample too small ({n} bars; need ≥10)",
        )

    ann = float(np.sqrt(_resolve_periods_per_year(r.index, tf)))
    vals = r.values.astype(float)
    fwd_sharpe = _sharpe_ann(vals, ann)
    fwd_total = float(np.prod(1.0 + vals) - 1.0)
    fwd_equity = np.cumprod(1.0 + vals)
    fwd_dd = _max_dd_magnitude(fwd_equity)
    fwd_vol = float(vals.std(ddof=1)) if n >= 2 else 0.0
    fwd_psr = _psr(fwd_sharpe, vals, ann)

    # CI-based assessment. If we lack the CI, just report fwd metrics.
    in_ci: bool | None = None
    z: float | None = None
    if (backtest_sharpe_ci_lo is not None
            and backtest_sharpe_ci_hi is not None
            and np.isfinite(backtest_sharpe_ci_lo)
            and np.isfinite(backtest_sharpe_ci_hi)):
        mid = 0.5 * (backtest_sharpe_ci_lo + backtest_sharpe_ci_hi)
        half = max(0.5 * (backtest_sharpe_ci_hi - backtest_sharpe_ci_lo), 1e-9)
        z = (fwd_sharpe - mid) / half
        in_ci = (backtest_sharpe_ci_lo <= fwd_sharpe <= backtest_sharpe_ci_hi)

    # Consecutive-days-below-CI tracking via rolling-30d Sharpe.
    consec = 0
    if (backtest_sharpe_ci_lo is not None
            and np.isfinite(backtest_sharpe_ci_lo) and n >= 30):
        rs = rolling_window_sharpe(r, window_days=30, tf=tf).dropna()
        if not rs.empty:
            below = (rs < backtest_sharpe_ci_lo).astype(int)
            # Count the tail streak of trues from the END.
            tail = below.values[::-1]
            streak_bars = 0
            for x in tail:
                if x == 1:
                    streak_bars += 1
                else:
                    break
            # Convert bars to days using index spacing.
            if streak_bars > 0:
                bar_seconds = (r.index[1] - r.index[0]).total_seconds()
                bars_per_day = max(1, int(round(86400.0 / bar_seconds)))
                consec = int(streak_bars / bars_per_day)

    # Flag decision.
    flag: DriftFlag = "ok"
    reason = "forward Sharpe inside CI"
    if z is not None and z < -1.5:
        flag = "alert"
        reason = (f"forward Sharpe {fwd_sharpe:.2f} far below backtest CI "
                  f"[{backtest_sharpe_ci_lo:.2f}, {backtest_sharpe_ci_hi:.2f}] "
                  f"(z={z:.2f})")
    elif consec >= consecutive_threshold_days:
        flag = "alert"
        reason = (f"rolling-30d Sharpe has been below backtest CI lower "
                  f"for {consec} consecutive days "
                  f"(threshold {consecutive_threshold_days})")
    elif z is not None and z < 0:
        flag = "warn"
        reason = (f"forward Sharpe {fwd_sharpe:.2f} below CI mid "
                  f"(z={z:.2f}); not yet alarming")
    elif consec >= 7:
        flag = "warn"
        reason = (f"rolling-30d Sharpe below CI lower for {consec} days "
                  f"(<{consecutive_threshold_days} → warn only)")

    return DriftReport(
        n_periods=int(n),
        forward_sharpe=float(fwd_sharpe),
        forward_total_return=float(fwd_total),
        forward_max_dd=float(fwd_dd),
        forward_volatility=float(fwd_vol),
        backtest_sharpe=backtest_oos_sharpe,
        backtest_sharpe_ci_lo=backtest_sharpe_ci_lo,
        backtest_sharpe_ci_hi=backtest_sharpe_ci_hi,
        sharpe_z_vs_backtest=z,
        in_ci=in_ci,
        consecutive_below_ci_days=int(consec),
        forward_psr=fwd_psr,
        flag=flag,
        flag_reason=reason,
    )
