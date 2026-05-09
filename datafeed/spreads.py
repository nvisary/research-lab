"""Per-symbol bid-ask spread estimation from 1m close prices.

Bybit's klines API does not expose bid-ask spread, but it can be
estimated from close-to-close serial covariance (Roll, 1984):

    spread_bps ≈ 2 * sqrt( -Cov(Δp_t, Δp_{t-1}) ) / mean_price * 10000

Intuition: in a market with a real bid-ask, consecutive trades alternate
between bid and ask, producing **negative** serial covariance in returns.
Roll's estimator inverts that relationship.

Caveats:
  - Estimator is undefined when sample Cov >= 0 (happens in trending or
    very thin samples); we fall back to a Corwin-Schultz-flavored proxy
    using the high-low range, which is always defined.
  - Estimator is noisy on short samples. Recommend ~50+ bars per bucket;
    we bucket by hour (~60 1m bars) for intraday liquidity profiles.
  - Estimator measures the EFFECTIVE spread paid by a price-taker, which
    is what we care about for backtest cost modeling. It can differ from
    the quoted top-of-book spread (effective ≤ quoted, because not every
    fill walks the full quote).

For backtesting, treat the result as a *floor* on slippage; size impact
(slippage as a function of order size vs depth) is layered on top in
harness/costs.py — see Phase 2.2.

References:
  Roll, R. (1984). "A Simple Implicit Measure of the Effective Bid-Ask
    Spread in an Efficient Market." Journal of Finance, 39(4).
  Corwin, S. A., & Schultz, P. (2012). "A Simple Way to Estimate Bid-Ask
    Spreads from Daily High and Low Prices." Journal of Finance, 67(2).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# A pragmatic floor: the cheapest perp pairs (BTC, ETH) on Bybit clear
# at ~0.5-1 bps effective during liquid hours. We never report below
# this, even on degenerate samples — it's safer to overstate spreads
# than understate them in a cost model.
SPREAD_FLOOR_BPS: float = 0.5

# Minimum bars per bucket for Roll to be even attempted. Below this we
# go straight to the high-low proxy.
MIN_BARS_FOR_ROLL: int = 30


def roll_spread_bps(close: pd.Series) -> tuple[float, bool]:
    """Roll-estimator of effective spread, in basis points.

    Returns (spread_bps, is_fallback). When sample serial covariance is
    non-negative (trending series, too few bars, etc.), the estimator is
    undefined; the caller can choose to use a fallback. We return the
    floor and flag the fallback so the caller knows.
    """
    s = pd.Series(close).dropna()
    if len(s) < MIN_BARS_FOR_ROLL:
        return SPREAD_FLOOR_BPS, True

    # Returns, not raw price diffs: scale-invariant across symbols and
    # tighter relationship to bps. Roll's original is on prices but the
    # ratio works with returns identically up to mean-price normalization.
    r = s.pct_change().dropna().values
    if len(r) < 2:
        return SPREAD_FLOOR_BPS, True

    # Lag-1 autocovariance. np.cov returns sample covariance with ddof=1.
    cov = float(np.cov(r[1:], r[:-1], ddof=1)[0, 1])
    if not np.isfinite(cov) or cov >= 0:
        return float("nan"), True   # caller should fallback

    spread_ret = 2.0 * np.sqrt(-cov)        # in return units
    spread_bps = spread_ret * 1e4
    return float(max(spread_bps, SPREAD_FLOOR_BPS)), False


def hl_proxy_spread_bps(df: pd.DataFrame) -> float:
    """Fallback: high-low range as a spread proxy.

    A simplified relative of Corwin-Schultz: the average intra-bar range
    bounds the realised spread from above (you can't pay more than the
    bar's swing on average over many bars), so we take a fraction of it.
    The 0.5 multiplier is empirical — calibrated so the proxy lands
    near Roll on liquid majors when both are well-defined.

    Expects df with `high`, `low`, `close`. Returns float bps.
    """
    if df.empty:
        return SPREAD_FLOOR_BPS
    rng = (df["high"] - df["low"]) / df["close"]
    rng = rng.replace([np.inf, -np.inf], np.nan).dropna()
    if rng.empty:
        return SPREAD_FLOOR_BPS
    proxy_bps = float(rng.mean()) * 0.5 * 1e4
    return max(proxy_bps, SPREAD_FLOOR_BPS)


def estimate_spread_series(df_1m: pd.DataFrame, bucket: str = "1h") -> pd.DataFrame:
    """Roll estimator bucketed by `bucket` (e.g. '1H', '4H', '1D').

    Input: df_1m with tz-aware UTC index and at least `close`. Optionally
    `high`, `low` for fallback.
    Output: DataFrame [bucket_start, spread_bps, n_bars, fallback_used].

    For each bucket:
      1. Try Roll on close.
      2. If Roll undefined (positive covariance, too few bars), use the
         high-low proxy if `high`/`low` present, else SPREAD_FLOOR_BPS.
    """
    if df_1m.empty or "close" not in df_1m.columns:
        return pd.DataFrame(columns=["bucket_start", "spread_bps", "n_bars", "fallback_used"])

    has_hl = "high" in df_1m.columns and "low" in df_1m.columns
    rows: list[dict] = []
    for bucket_start, chunk in df_1m.groupby(pd.Grouper(freq=bucket)):
        if chunk.empty:
            continue
        spread_bps, is_fb = roll_spread_bps(chunk["close"])
        if is_fb or not np.isfinite(spread_bps):
            if has_hl:
                spread_bps = hl_proxy_spread_bps(chunk)
            else:
                spread_bps = SPREAD_FLOOR_BPS
            fallback = True
        else:
            fallback = False
        rows.append({
            "bucket_start": bucket_start,
            "spread_bps": float(spread_bps),
            "n_bars": int(len(chunk)),
            "fallback_used": bool(fallback),
        })
    return pd.DataFrame(rows)


def summarize(spread_df: pd.DataFrame) -> dict:
    """Diagnostic aggregate for a per-bucket spread DataFrame."""
    if spread_df.empty:
        return {"mean_bps": float("nan"), "median_bps": float("nan"),
                "p95_bps": float("nan"), "fallback_pct": float("nan"),
                "n_buckets": 0}
    s = spread_df["spread_bps"]
    return {
        "mean_bps": float(s.mean()),
        "median_bps": float(s.median()),
        "p95_bps": float(s.quantile(0.95)),
        "fallback_pct": float(spread_df["fallback_used"].mean() * 100.0),
        "n_buckets": int(len(spread_df)),
    }
