"""Performance metrics from a portfolio equity curve."""
from __future__ import annotations

import warnings

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
    "1w":    365.25 / 7,              # 52.1786  (Julian year, consistent with rest of table)
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
    # accept '60min' / '1H' / '1Min' aliases via pandas. Note that any
    # parseable Timedelta string is accepted silently — including
    # non-canonical cadences like "2h30min" — so callers should prefer
    # the canonical strings in TF_PERIODS_PER_YEAR when possible.
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
    # Final fallback path: infer the canonical bar period from the
    # index spacing. This is the SILENT-DEFLATION risk flagged in
    # MATH_AUDIT.md H2 — when the index has gaps, dt_seconds is the
    # *average* spacing and overstates the bar period, shrinking the
    # annualisation factor and under-reporting Sharpe. Emit a
    # RuntimeWarning so callers know they're on the gap-deflated path.
    warnings.warn(
        f"_resolve_periods_per_year falling back to index-inferred dt "
        f"({dt_seconds:.0f}s); tf was {tf!r}. With gappy data this "
        f"under-reports Sharpe. Pass a canonical tf in TF_PERIODS_PER_YEAR.",
        RuntimeWarning, stacklevel=2,
    )
    return (365.25 * 24 * 3600) / dt_seconds


# kept for backward-compat — call sites that haven't been threaded yet still work
def _annualization_factor(index: pd.DatetimeIndex, tf: str | None = None) -> float:
    return _resolve_periods_per_year(index, tf)


def sharpe(returns: pd.Series, tf: str | None = None) -> float:
    """Annualized Sharpe ratio with Bessel-corrected (ddof=1) std.

    Convention: ``ddof=1`` is used here AND in every other estimator in
    `harness/` so the values are comparable. Effect vs the biased MLE
    ddof=0 is a factor √(n/(n-1)) ≈ 0.5–1% at n in [100, 300] — Sharpe
    is shrunk slightly relative to the legacy MLE form.
    """
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    if sd == 0:
        return 0.0
    return float(r.mean() / sd * np.sqrt(_resolve_periods_per_year(r.index, tf)))


def sortino(returns: pd.Series, tf: str | None = None) -> float:
    """Annualized Sortino. Returns ``inf`` when there are no losing bars and
    the mean return is positive (zero-downside-risk, positive-edge case) —
    mirrors the ``profit_factor`` convention so the "no losses" case is
    monotone in strategy quality. Returns ``0.0`` for degenerate (n<2) or
    zero-mean-and-no-loss inputs.
    """
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    downside = r[r < 0]
    if len(downside) == 0:
        return float("inf") if r.mean() > 0 else 0.0
    # ddof=1 requires len(downside) >= 2; with one loss the unbiased
    # variance is undefined and we treat it the same as zero downside.
    if len(downside) < 2:
        return float("inf") if r.mean() > 0 else 0.0
    sd = downside.std(ddof=1)
    if sd == 0:
        return 0.0
    return float(r.mean() / sd * np.sqrt(_resolve_periods_per_year(r.index, tf)))


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
    """Average daily **target** turnover (sum of |Δtarget_position| per day,
    averaged across days).

    Note this counts every bar-over-bar shift in the *target* weight, not
    realised executed trades — for strategies with continuous signals
    (e.g. continuous CSM, z-band drift) the number reported here is
    substantially larger than the count of fills in the trade ledger
    suggests, because vbt only fires a trade when the target crosses a
    fill threshold. The metric is emitted under the dict key
    ``target_turnover`` in summary() to make this distinction explicit;
    do NOT use it as a cost-model input.
    """
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


def profit_factor(trade_pnls: pd.Series) -> float:
    """Σ(wins) / |Σ(losses)|.

    Returns NaN if there were no trades, ``inf`` if there were no losers
    (all-positive). PF < 1.0 means cumulative losses dominate wins; the
    "industry rule of thumb" is PF > 1.5 = decent, > 2.0 = strong.
    """
    p = pd.Series(trade_pnls).dropna()
    if p.empty:
        return float("nan")
    wins_sum = float(p[p > 0].sum())
    losses_sum = float(-p[p < 0].sum())
    if losses_sum == 0:
        return float("inf") if wins_sum > 0 else float("nan")
    return wins_sum / losses_sum


def expectancy(trade_pnls: pd.Series) -> float:
    """Mean PnL per trade in quote-currency units (USD).

    This is the canonical expectancy: ``E[trade]``. Equivalent to
    ``WR·AvgWin − LR·AvgLoss`` but computed directly so it doesn't
    suffer from rounding discrepancies. Compare against estimated
    per-trade cost (fees + slippage) — a strategy whose expectancy
    is below cost is structurally a money-loser.
    """
    p = pd.Series(trade_pnls).dropna()
    if p.empty:
        return float("nan")
    return float(p.mean())


def avg_win_loss(trade_pnls: pd.Series) -> tuple[float, float]:
    """Mean win and mean loss (both as positive numbers).

    Returns ``(avg_win, avg_loss)``. ``avg_loss`` is positive (so
    payoff ratio is ``avg_win / avg_loss`` directly). NaN when the
    side is empty.
    """
    p = pd.Series(trade_pnls).dropna()
    wins = p[p > 0]
    losses = p[p < 0]
    aw = float(wins.mean()) if not wins.empty else float("nan")
    al = float(-losses.mean()) if not losses.empty else float("nan")
    return aw, al


def var_cvar(returns: pd.Series, levels: tuple[float, ...] = (0.95, 0.99),
             tf: str | None = None) -> dict:
    """Value-at-Risk and Conditional VaR (Expected Shortfall) at given levels.

    Both expressed as positive fractions.

    Resolution policy (matters for inter-strategy comparability):
      * When ``tf`` indicates >= 2 bars/day, bar-level returns are
        compounded to daily before the percentile is taken — output
        keys are ``var_95`` / ``cvar_95`` / ``var_99`` / ``cvar_99``,
        all in the conventional daily unit.
      * When ``tf`` indicates < ~1.5 bars/day (daily and longer cadences
        like 1d / 1w / 1M), no resample is possible without inventing
        data. Output keys are ``var_per_bar_95`` / ``cvar_per_bar_95``
        / ``var_per_bar_99`` / ``cvar_per_bar_99`` to flag that these
        are per-bar tail estimates, NOT daily. Mixing them with
        sub-daily strategies' ``var_95`` would compare a weekly tail
        against a daily tail.
      * When ``tf`` is None or unrecognised, falls back to the legacy
        daily-named keys with no resample (existing behaviour, lossy
        on coarse data — emit a docstring warning rather than break
        callers that read ``var_95`` directly).

    Values are ``None`` when the sample is too small (<20 obs after
    any resampling) to make the tail estimate meaningful.
    """
    daily_keyed = True
    if tf and tf in TF_PERIODS_PER_YEAR:
        bars_per_day = TF_PERIODS_PER_YEAR[tf] / 365.25
        # Anything coarser than ~1.5 bars/day cannot be reasonably
        # compounded to daily (would yield <1 daily obs per period).
        daily_keyed = bars_per_day >= 1.0

    def _key(pct: int, name: str) -> str:
        # name is "var" or "cvar"
        return f"{name}_{pct}" if daily_keyed else f"{name}_per_bar_{pct}"

    out: dict[str, float | None] = {}
    for lev in levels:
        pct = int(round(lev * 100))
        out[_key(pct, "var")] = None
        out[_key(pct, "cvar")] = None
    r = pd.Series(returns).dropna()
    if r.empty:
        return out
    if tf and tf in TF_PERIODS_PER_YEAR:
        bars_per_day = TF_PERIODS_PER_YEAR[tf] / 365.25
        if bars_per_day > 1.5:
            r = ((1 + r).resample("1D").prod() - 1).dropna()
    if len(r) < 20:
        return out
    for lev in levels:
        pct = int(round(lev * 100))
        # Lower-tail percentile of returns — i.e. the (1-lev)-th quantile.
        thresh = float(r.quantile(1.0 - lev))
        var = -thresh  # positive number
        tail = r[r <= thresh]
        cvar = float(-tail.mean()) if not tail.empty else var
        out[_key(pct, "var")] = float(var)
        out[_key(pct, "cvar")] = float(cvar)
    return out


def information_ratio(returns: pd.Series, bench_returns: pd.Series,
                      tf: str | None = None) -> float:
    """Annualized Information Ratio vs a benchmark.

    ``IR = mean(strat - bench) / std(strat - bench) · √periods_per_year``

    The institutional analog of "alpha-Sharpe": measures excess return
    per unit of *tracking* volatility. >0.5 is decent, >1.0 is strong
    (over the same horizon and benchmark). Aligns the two series on
    their common index and drops bars where either is missing.
    """
    s = pd.Series(returns).dropna()
    b = pd.Series(bench_returns).dropna()
    if s.empty or b.empty:
        return 0.0
    aligned = pd.concat([s, b], axis=1, join="inner").dropna()
    if len(aligned) < 30:
        return 0.0
    excess = aligned.iloc[:, 0] - aligned.iloc[:, 1]
    sd = float(excess.std(ddof=1))
    if sd == 0:
        return 0.0
    return float(excess.mean() / sd
                 * np.sqrt(_resolve_periods_per_year(excess.index, tf)))


def _longest_true_run(arr: np.ndarray) -> int:
    """Length of the longest contiguous True run in a boolean array."""
    if len(arr) == 0:
        return 0
    best = cur = 0
    for v in arr:
        if v:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return int(best)


def quality_metrics(equity: pd.Series, returns: pd.Series,
                    positions: pd.DataFrame | None,
                    trades_in_slice: pd.DataFrame | None,
                    tf: str | None = None) -> dict:
    """Strategy-quality / problem-detection metrics.

    Each ticks a specific class of failure that raw Sharpe + max_dd hide:
      - pct_positive_months    consistency vs lucky-streak
      - longest_underwater_*   pain duration / recovery time
      - pnl_concentration_*    edge dependence on a few outliers
      - tail_ratio             skew of bar-level returns
      - pain_index             severity-over-time (Ulcer-style)
      - pct_time_in_position   does it actually trade or sit in cash
      - avg/median_trade_duration_hours
      - skew / kurt            distribution shape (PSR uses these but UI wants them)

    Designed to gracefully degrade: empty inputs return Nones, never raise.
    """
    out: dict = {
        "pct_positive_months": None,
        "longest_underwater_bars": None,
        "longest_underwater_days": None,
        "pnl_concentration_top5_pct": None,
        "pnl_concentration_top1_pct": None,
        "tail_ratio": None,
        "pain_index": None,
        "pct_time_in_position": None,
        "avg_trade_duration_hours": None,
        "median_trade_duration_hours": None,
        "skew": None,
        "kurt": None,
        "profit_factor": None,
        "expectancy": None,
        "avg_win": None,
        "avg_loss": None,
        "var_95": None,
        "cvar_95": None,
        "var_99": None,
        "cvar_99": None,
    }
    # ---- Monthly consistency ----
    if equity is not None and len(equity) >= 2:
        try:
            # resample("MS").last() labels by month-start; the first bucket's
            # .last() is the last equity value within a *partial* month
            # (everything from equity.index[0] to end-of-that-month). If we
            # included it in pct_change(), the first reported monthly return
            # would mix partial-month-1 with month-2 — inflating the apparent
            # return on whichever side has more bars. Drop the first label
            # explicitly so reported monthly returns are over full calendar
            # months only. Mirrors the heatmap fix in commit 0d034cd.
            monthly_eq = equity.resample("MS").last().dropna()
            if len(monthly_eq) >= 2:
                monthly_eq = monthly_eq.iloc[1:]
            if len(monthly_eq) >= 2:
                m_ret = monthly_eq.pct_change().dropna()
                if len(m_ret) >= 1:
                    out["pct_positive_months"] = float((m_ret > 0).mean() * 100.0)
        except Exception:
            pass

    # ---- Longest underwater run ----
    if equity is not None and len(equity) >= 2:
        peak = equity.cummax()
        under = (equity < peak).values
        run_bars = _longest_true_run(under)
        out["longest_underwater_bars"] = run_bars
        # Convert to calendar days using TF if known.
        bars_per_day = TF_PERIODS_PER_YEAR.get(tf, 0) / 365.25 if tf else 0
        if bars_per_day > 0:
            out["longest_underwater_days"] = float(run_bars / bars_per_day)

    # ---- Pain index (Ulcer) ----
    if equity is not None and len(equity) >= 2:
        peak = equity.cummax()
        dd_frac = (equity / peak - 1.0).clip(upper=0.0)  # negative or zero
        out["pain_index"] = float(np.sqrt((dd_frac ** 2).mean()))

    # ---- Trade-PnL concentration ----
    if trades_in_slice is not None and not trades_in_slice.empty \
            and "pnl_quote" in trades_in_slice.columns:
        pnl = trades_in_slice["pnl_quote"].dropna()
        if not pnl.empty:
            total = float(pnl.sum())
            if abs(total) > 1e-9:
                # Top-N positive contributions / |total|. We use abs(total) so
                # losing strategies' "concentration" still reads meaningfully
                # (a -10% strategy where one trade made +5% is concentrated).
                pos = pnl[pnl > 0].sort_values(ascending=False)
                if len(pos) >= 1:
                    out["pnl_concentration_top1_pct"] = float(
                        pos.iloc[0] / abs(total) * 100.0
                    )
                if len(pos) >= 5:
                    out["pnl_concentration_top5_pct"] = float(
                        pos.iloc[:5].sum() / abs(total) * 100.0
                    )
                else:
                    out["pnl_concentration_top5_pct"] = float(
                        pos.sum() / abs(total) * 100.0
                    )

    # ---- Trade durations ----
    if trades_in_slice is not None and not trades_in_slice.empty \
            and "duration_hours" in trades_in_slice.columns:
        dh = trades_in_slice["duration_hours"].dropna()
        if not dh.empty:
            out["avg_trade_duration_hours"] = float(dh.mean())
            out["median_trade_duration_hours"] = float(dh.median())

    # ---- Tail ratio ----
    if returns is not None and len(returns.dropna()) >= 50:
        r = returns.dropna()
        # Top/bottom decile of returns. Take abs of mean so the ratio is
        # always comparable; <1 means losses dwarf gains in the tails.
        top = r.quantile(0.9)
        bot = r.quantile(0.1)
        top_mean = r[r >= top].mean()
        bot_mean = abs(r[r <= bot].mean())
        if bot_mean > 1e-12:
            out["tail_ratio"] = float(top_mean / bot_mean)

    # ---- % time in position ----
    if positions is not None and not positions.empty:
        try:
            in_pos = (positions != 0).any(axis=1)
            out["pct_time_in_position"] = float(in_pos.mean() * 100.0)
        except Exception:
            pass

    # ---- Skew / kurt ----
    if returns is not None and len(returns.dropna()) >= 30:
        r = returns.dropna()
        try:
            out["skew"] = float(r.skew())
            out["kurt"] = float(r.kurt())
        except Exception:
            pass

    # ---- Trade-shape metrics (PF / expectancy / mean win-loss) ----
    if trades_in_slice is not None and not trades_in_slice.empty \
            and "pnl_quote" in trades_in_slice.columns:
        pnl = trades_in_slice["pnl_quote"].dropna()
        if not pnl.empty:
            try:
                pf = profit_factor(pnl)
                # JSON cannot serialize inf — clamp to a large sentinel.
                out["profit_factor"] = (None if (isinstance(pf, float)
                                                 and (np.isnan(pf) or np.isinf(pf)))
                                        else float(pf))
                out["expectancy"] = float(expectancy(pnl))
                aw, al = avg_win_loss(pnl)
                out["avg_win"] = (None if np.isnan(aw) else float(aw))
                out["avg_loss"] = (None if np.isnan(al) else float(al))
            except Exception:
                pass

    # ---- VaR / CVaR (daily, 95% and 99%) ----
    if returns is not None and len(returns.dropna()) >= 20:
        try:
            vc = var_cvar(returns, levels=(0.95, 0.99), tf=tf)
            out.update(vc)
        except Exception:
            pass

    return out


def capacity_metrics(trades_in_slice: pd.DataFrame,
                     warn_threshold_pct: float = 5.0) -> dict:
    """Trade-size-vs-volume capacity diagnostics.

    Operates on a trades DataFrame with a ``participation_pct`` column
    (added in harness/backtest.py from daily $-volume divided into
    entry notional). Returns NaN-filled dict if column is missing or
    all values are NaN — i.e. no volume data was available.

    A strategy whose ``max_participation_pct > 5%`` will, in live
    trading, materially move price against itself; the static cost
    model under-charges. Surface this so the operator can either cap
    size, pick more liquid symbols, or accept the bias explicitly.
    """
    out = {
        "max_participation_pct": None,
        "mean_participation_pct": None,
        "pct_trades_over_threshold": None,
        "n_trades_over_threshold": 0,
        "capacity_threshold_pct": warn_threshold_pct,
    }
    if trades_in_slice is None or trades_in_slice.empty:
        return out
    if "participation_pct" not in trades_in_slice.columns:
        return out
    p = trades_in_slice["participation_pct"].dropna()
    if p.empty:
        return out
    over = p > warn_threshold_pct
    out["max_participation_pct"] = float(p.max())
    out["mean_participation_pct"] = float(p.mean())
    out["pct_trades_over_threshold"] = float(over.mean() * 100.0)
    out["n_trades_over_threshold"] = int(over.sum())
    return out


def summary(equity: pd.Series, returns: pd.Series, positions: pd.DataFrame,
            n_trades: int, tf: str | None = None,
            benchmark: pd.Series | None = None,
            trades_in_slice: pd.DataFrame | None = None,
            seed_hint: int | None = None) -> dict:
    """Per-window/per-iter metrics summary.

    ``seed_hint`` is forwarded to ``bootstrap_sharpe_ci`` so the CI is
    reproducible for a given (iter, window) but varies across iters —
    callers (e.g. runner.iterate) pass an iter-derived hash so the
    history-table CI column shows the natural draw-to-draw drift instead
    of the same number repeated forever. ``None`` keeps the legacy
    fixed-seed behaviour for ad-hoc callers.
    """
    # PSR is computed inline; DSR (which needs n_trials) is added by the caller.
    from harness.stats import psr as _psr, bootstrap_sharpe_ci as _ci
    sh = sharpe(returns, tf=tf)
    psr_value = _psr(returns, tf=tf) if len(returns.dropna()) >= 30 else 0.0
    # Derive a per-call seed from seed_hint when provided. Hashing with
    # n_boot keeps it distinct if n_boot is ever sweeped per call.
    if seed_hint is not None:
        ci_seed = int(hash((int(seed_hint), 400)) & 0xFFFFFFFF)
    else:
        ci_seed = None  # bootstrap_sharpe_ci default (seed=42) applies
    try:
        if len(returns.dropna()) >= 100:
            ci_kwargs = {"n_boot": 400, "tf": tf}
            if ci_seed is not None:
                ci_kwargs["seed"] = ci_seed
            ci_lo, ci_hi = _ci(returns, **ci_kwargs)
        else:
            ci_lo, ci_hi = sh, sh
    except Exception:
        ci_lo, ci_hi = sh, sh
    bench_sh: float | None = None
    info_ratio: float | None = None
    if benchmark is not None and len(benchmark.dropna()) > 1:
        bench_ret = benchmark.pct_change()
        bench_sh = float(sharpe(bench_ret, tf=tf))
        try:
            info_ratio = float(information_ratio(returns, bench_ret, tf=tf))
        except Exception:
            info_ratio = None
    cap = capacity_metrics(trades_in_slice)
    qual = quality_metrics(equity, returns, positions, trades_in_slice, tf=tf)
    return {
        "sharpe": sh,
        "bench_sharpe": bench_sh,
        "alpha_sharpe": (sh - bench_sh) if bench_sh is not None else None,
        "information_ratio": info_ratio,
        "sortino": sortino(returns, tf=tf),
        "calmar": calmar(equity),
        "cagr": cagr(equity),
        "max_dd": max_drawdown(equity),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) else 0.0,
        "target_turnover": turnover(positions),
        "hit_rate": hit_rate(returns),
        "n_trades": int(n_trades),
        "n_periods": int(len(returns)),
        "psr": float(psr_value),
        "sharpe_ci_lo": float(ci_lo),
        "sharpe_ci_hi": float(ci_hi),
        **cap,
        **qual,
    }


def composite_score(metrics: dict, dd_penalty: float = 0.5,
                    min_trades: int = 50, low_trades_penalty: float = 0.5,
                    min_time_in_position: float = 20.0,
                    time_in_position_penalty: float = 1.0) -> float:
    """OOS_Sharpe − λ·MaxDD with low-activity and low-time-in-position penalties.

    Below ``min_trades`` we apply a graded penalty
        ``low_trades_penalty * (1 - sqrt(n / min_trades))``
    so a strategy with 49 trades is essentially unpenalized while a strategy
    with 5 trades pays roughly 2/3 of the full penalty.

    Below ``min_time_in_position`` (percent of bars with any non-zero position)
    we apply a linear penalty
        ``time_in_position_penalty * (1 - tip / min_time_in_position)``
    so a strategy that sits in cash 95% of the time pays nearly the full
    penalty. This blocks the "Sharpe-inflation-via-flat-equity" gaming
    pattern: when ``pct_time_in_position`` collapses, variance also
    collapses and ``mean / std`` Sharpe inflates on micro-drift. The
    penalty cliff is intentional — composite is a research filter, not
    an academic objective.

    n=0 remains ``-∞`` (ineligible).
    """
    import math
    sh = metrics.get("sharpe", 0.0)
    dd = metrics.get("max_dd", 0.0)
    n = metrics.get("n_trades", 0)
    if n == 0:
        return float("-inf")
    score = sh - dd_penalty * dd
    # The ``if n < min_trades`` guard is LOAD-BEARING: without it,
    # ``deficit = 1 - sqrt(n / min_trades)`` becomes negative for
    # n > min_trades, which would silently *reward* high-trade-count
    # strategies via the subtraction. The penalty is intentionally
    # one-sided — once you've cleared the activity floor, more trades
    # do not earn additional composite.
    if n < min_trades:
        deficit = 1.0 - math.sqrt(n / min_trades)
        score -= low_trades_penalty * deficit
    # Time-in-position floor. ``pct_time_in_position`` is the fraction
    # of bars where at least one symbol holds a non-zero position,
    # expressed as a percentage (0..100). When None (older metric blobs
    # without this field), skip — no penalty applied.
    tip = metrics.get("pct_time_in_position")
    if tip is not None and tip < min_time_in_position:
        tip_deficit = 1.0 - max(float(tip), 0.0) / float(min_time_in_position)
        score -= time_in_position_penalty * tip_deficit
    return float(score)


def aggregate_wf_composite(window_metrics: list[dict],
                           dd_penalty: float = 0.5,
                           min_trades: int = 50,
                           low_trades_penalty: float = 0.5,
                           stability_penalty: float = 0.5,
                           min_time_in_position: float = 20.0,
                           time_in_position_penalty: float = 1.0) -> tuple[float, dict]:
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

    composites = [composite_score(m, dd_penalty, min_trades, low_trades_penalty,
                                   min_time_in_position, time_in_position_penalty)
                  for m in window_metrics]
    if not composites or any(c == float("-inf") for c in composites):
        return float("-inf"), {
            "mean_sharpe": 0.0, "std_sharpe": 0.0, "median_sharpe": 0.0,
            "mean_max_dd": 0.0, "worst_max_dd": 0.0,
            "mean_n_trades": 0.0, "n_windows": len(window_metrics),
        }

    mean_c = float(np.mean(composites))
    # ddof=1 is undefined for n=1 (NaN); fall back to 0 stability term
    # for the degenerate single-window case so score == mean_c.
    std_c = float(np.std(composites, ddof=1)) if len(composites) >= 2 else 0.0
    score = mean_c - stability_penalty * std_c

    sharpes = [m.get("sharpe", 0.0) for m in window_metrics]
    dds = [m.get("max_dd", 0.0) for m in window_metrics]
    trades = [m.get("n_trades", 0) for m in window_metrics]
    # Capacity: max-of-max across windows is the conservative aggregate.
    # mean-of-means averages out per-window noise. A strategy is
    # capacity-limited if ANY window touches the threshold.
    max_parts = [m.get("max_participation_pct") for m in window_metrics]
    max_parts_clean = [p for p in max_parts if p is not None]
    mean_parts = [m.get("mean_participation_pct") for m in window_metrics]
    mean_parts_clean = [p for p in mean_parts if p is not None]
    n_over = sum(int(m.get("n_trades_over_threshold", 0) or 0) for m in window_metrics)
    # Quality / problem-detection aggregates. Each metric uses the
    # appropriate summary across windows: worst-case (max) for pain
    # indicators, mean for distribution properties, max for
    # concentration. None values are skipped.
    def _agg(key: str, fn):
        vals = [m.get(key) for m in window_metrics]
        clean = [v for v in vals if v is not None]
        return float(fn(clean)) if clean else None

    qual_agg = {
        "mean_pct_positive_months": _agg("pct_positive_months", np.mean),
        "worst_longest_underwater_bars": _agg("longest_underwater_bars", np.max),
        "worst_longest_underwater_days": _agg("longest_underwater_days", np.max),
        "worst_pnl_concentration_top5_pct": _agg("pnl_concentration_top5_pct", np.max),
        "worst_pnl_concentration_top1_pct": _agg("pnl_concentration_top1_pct", np.max),
        "mean_tail_ratio": _agg("tail_ratio", np.mean),
        "worst_pain_index": _agg("pain_index", np.max),
        "mean_pct_time_in_position": _agg("pct_time_in_position", np.mean),
        "mean_avg_trade_duration_hours": _agg("avg_trade_duration_hours", np.mean),
        "mean_skew": _agg("skew", np.mean),
        "mean_kurt": _agg("kurt", np.mean),
        # Sharpe gap: max across windows (worst-case overfit signal).
        "worst_sharpe_gap": _agg("sharpe_gap", np.max),
        "mean_sharpe_gap": _agg("sharpe_gap", np.mean),
        # Trade-shape metrics: mean across windows for the central
        # tendency, plus min PF as the conservative aggregate (one
        # bad window with PF<1 sinks the strategy).
        "mean_profit_factor": _agg("profit_factor", np.mean),
        "min_profit_factor": _agg("profit_factor", np.min),
        "mean_expectancy": _agg("expectancy", np.mean),
        "mean_avg_win": _agg("avg_win", np.mean),
        "mean_avg_loss": _agg("avg_loss", np.mean),
        # Tail risk: worst-case (max) is the conservative aggregate.
        "worst_var_95": _agg("var_95", np.max),
        "worst_cvar_95": _agg("cvar_95", np.max),
        "worst_var_99": _agg("var_99", np.max),
        "worst_cvar_99": _agg("cvar_99", np.max),
        # Information ratio vs benchmark: mean and median.
        "mean_information_ratio": _agg("information_ratio", np.mean),
        "median_information_ratio": _agg("information_ratio", np.median),
    }
    cagrs = [m.get("cagr", 0.0) for m in window_metrics]
    total_returns = [m.get("total_return", 0.0) for m in window_metrics]
    bench_sharpes = [m.get("bench_sharpe") for m in window_metrics]
    alphas = [m.get("alpha_sharpe") for m in window_metrics]
    bench_sharpes_clean = [s for s in bench_sharpes if s is not None]
    alphas_clean = [a for a in alphas if a is not None]
    return score, {
        "mean_sharpe": float(np.mean(sharpes)),
        "std_sharpe": float(np.std(sharpes, ddof=1)) if len(sharpes) >= 2 else 0.0,
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
        "max_participation_pct": (float(np.max(max_parts_clean))
                                  if max_parts_clean else None),
        "mean_participation_pct": (float(np.mean(mean_parts_clean))
                                   if mean_parts_clean else None),
        "n_trades_over_threshold": int(n_over),
        **qual_agg,
    }
