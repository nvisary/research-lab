"""Stat-arb composite score.

Wraps `harness.metrics.composite_score` and adds two structural
penalties that catch the failure modes specific to stat-arb research:

  - **survival_penalty**: when fewer than 50% of fitted baskets survive
    to ≥ 50% of their planned lifespan, the strategy is fitting noise
    into one-week-then-broken structures. Even if the aggregated equity
    looks fine in-sample (diversification mask), the OOS reality is
    almost-certain decay. Penalty kicks in only below the 50% floor and
    grows linearly to a full unit at survival_rate = 0.

  - **half_life_penalty**: when the median fitted half-life of basket
    spreads exceeds `target_half_life` (= refit_freq / 4 by default),
    the basket cannot revert within the refit cycle and the strategy
    is structurally trading momentum on its own residual. Penalty
    grows linearly with excess, capped.

These penalties operate on the `statarb` block written by
`harness_statarb.backtest.run_statarb`. They are intentionally
simple — one parameter each — to avoid plumping the judge with degrees
of freedom that obscure what the score is measuring.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from harness.metrics import composite_score as base_composite_score


def survival_penalty(
    survival_rate: float | None,
    threshold: float = 0.5,
    weight: float = 1.0,
) -> float:
    """Linear penalty for baskets that die before reaching threshold lifespan.

    Returns 0 above threshold, weight·(threshold − survival_rate)/threshold below.
    NaN / None survival_rate → 0 (no penalty, but a separate diagnostic flag
    will fire to surface the missing data).
    """
    if survival_rate is None or (isinstance(survival_rate, float) and math.isnan(survival_rate)):
        return 0.0
    sr = float(survival_rate)
    if sr >= threshold:
        return 0.0
    deficit = (threshold - sr) / threshold
    return weight * max(0.0, min(1.0, deficit))


def half_life_penalty(
    median_half_life_bars: float | None,
    target_half_life_bars: float,
    weight: float = 0.5,
    cap_multiplier: float = 3.0,
) -> float:
    """Linear penalty when median half-life exceeds target, capped.

    If median_hl ≤ target → 0. If median_hl = target · (1 + k) → weight·k,
    clamped at weight · (cap_multiplier - 1).

    None / NaN → 0 (no penalty; missing-data flag surfaces separately).
    inf median half-life → full cap.
    """
    if median_half_life_bars is None:
        return 0.0
    hl = float(median_half_life_bars)
    if math.isnan(hl):
        return 0.0
    if target_half_life_bars <= 0:
        return 0.0
    if math.isinf(hl):
        return weight * (cap_multiplier - 1.0)
    ratio = hl / target_half_life_bars
    if ratio <= 1.0:
        return 0.0
    excess = min(ratio - 1.0, cap_multiplier - 1.0)
    return weight * excess


def statarb_composite_score(
    metrics: dict,
    statarb: dict,
    refit_freq_bars: int,
    dd_penalty: float = 0.5,
    min_trades: int = 50,
    low_trades_penalty: float = 0.5,
    min_time_in_position: float = 20.0,
    time_in_position_penalty: float = 1.0,
    stitched_weight: float = 1.0,
    survival_threshold: float = 0.5,
    survival_weight: float = 1.0,
    half_life_weight: float = 0.5,
    target_half_life_ratio: float = 0.25,
) -> float:
    """Stat-arb composite. Returns float (-inf for ineligible strategies).

    Composition:
      base       = harness.metrics.composite_score(oos_metrics, ...)
      penalties  = survival_penalty(...) + half_life_penalty(...)
      composite  = base − penalties

    The hard sign-agreement clamp from base_composite (total_return ≤ 0
    forces composite ≤ total_return) still applies, since it's baked
    into the base call.
    """
    base = base_composite_score(
        metrics,
        dd_penalty=dd_penalty,
        min_trades=min_trades,
        low_trades_penalty=low_trades_penalty,
        min_time_in_position=min_time_in_position,
        time_in_position_penalty=time_in_position_penalty,
        stitched_weight=stitched_weight,
    )
    if base == float("-inf"):
        return base
    sr = statarb.get("survival_rate") if statarb else None
    hl = statarb.get("median_half_life_bars") if statarb else None
    target_hl = max(1.0, float(refit_freq_bars) * float(target_half_life_ratio))
    pen_survival = survival_penalty(sr, threshold=survival_threshold, weight=survival_weight)
    pen_half_life = half_life_penalty(hl, target_half_life_bars=target_hl, weight=half_life_weight)
    return float(base - pen_survival - pen_half_life)


def aggregate_wf_statarb_composite(
    windows: list[dict],
    refit_freq_bars: int,
    stability_penalty: float = 0.5,
    **kwargs,
) -> tuple[float, dict]:
    """Walk-forward aggregator for the stat-arb composite.

    `windows` is a list of `_run_one` return dicts (each has 'oos' and
    'statarb' sub-dicts). Score is mean − stability_penalty · std of
    per-window stat-arb composites.
    """
    per_window = []
    for w in windows:
        oos = w.get("oos") or {}
        sa = w.get("statarb") or {}
        c = statarb_composite_score(oos, sa, refit_freq_bars=refit_freq_bars, **kwargs)
        per_window.append(c)
    if not per_window or any(c == float("-inf") for c in per_window):
        return float("-inf"), {
            "per_window": per_window,
            "mean": None,
            "std": None,
        }
    mean = float(np.mean(per_window))
    std = float(np.std(per_window, ddof=1)) if len(per_window) >= 2 else 0.0
    score = mean - stability_penalty * std
    return float(score), {
        "per_window": [float(c) for c in per_window],
        "mean": mean,
        "std": std,
    }
