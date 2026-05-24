"""Stat-arb diagnostic flags.

Like `harness/diagnostics.py`'s ✓/⚠/✗ summary lines, but for the
structural attributes of stat-arb backtests: how well baskets persist,
how their fitted half-life compares to the refit cadence, and how much
diversification the discovery process actually delivers.

Flags are short one-liners surfaced to the agent on every iter so they
can scan structural health before opening the full tearsheet.
"""
from __future__ import annotations

import math
from typing import Any


def _fmt_float(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float) and math.isnan(x):
        return "n/a"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:.{digits}f}"


def build_flags(
    statarb: dict | None,
    refit_freq_bars: int,
    survival_threshold: float = 0.5,
    target_half_life_ratio: float = 0.25,
) -> list[str]:
    """One-line flags summarising the statarb block.

    Convention (matches harness/diagnostics):
      ✓  good
      ⚠  borderline / warning
      ✗  fail
      ℹ  informational
    """
    flags: list[str] = []
    if not statarb:
        return ["ℹ no statarb diagnostics produced (engine did not populate)"]

    n_events = statarb.get("n_events", 0)
    sr = statarb.get("survival_rate")
    hl = statarb.get("median_half_life_bars")
    realised = statarb.get("median_realized_lifespan_bars")
    log = statarb.get("discovery_log", [])

    # --- 1. Basket count / discovery activity ---
    n_active_seen = sum(ev.get("n_active_after", 0) for ev in log)
    n_proposed_total = sum(ev.get("n_proposed", 0) for ev in log)
    n_accepted_total = sum(ev.get("n_accepted", 0) for ev in log)
    if n_events == 0:
        flags.append("✗ no baskets were ever fitted — strategy filters too strict")
    elif n_events < 5:
        flags.append(f"⚠ only {n_events} basket events on the period — sample too small for stable inference")
    else:
        flags.append(f"✓ {n_events} basket events fitted, {n_accepted_total} acceptances / {n_proposed_total} proposed")

    # --- 2. Survival rate ---
    if sr is None or (isinstance(sr, float) and math.isnan(sr)):
        flags.append("ℹ survival_rate could not be computed (no closed events)")
    elif sr < survival_threshold:
        flags.append(
            f"✗ survival_rate {_fmt_float(sr)} < {survival_threshold:.2f} — "
            f"baskets dying before reaching planned lifespan"
        )
    elif sr < 0.75:
        flags.append(f"⚠ survival_rate {_fmt_float(sr)} (acceptable but not great)")
    else:
        flags.append(f"✓ survival_rate {_fmt_float(sr)}")

    # --- 3. Half-life vs refit cadence ---
    target_hl = max(1.0, refit_freq_bars * target_half_life_ratio)
    if hl is None:
        flags.append("ℹ median_half_life unavailable (no fitted half-life recorded)")
    elif math.isinf(hl):
        flags.append("✗ median_half_life = ∞ — spreads aren't reverting; structure is momentum, not mean-reversion")
    elif hl > target_hl * 2:
        flags.append(
            f"✗ median_half_life {_fmt_float(hl, 1)} bars > 2·target ({2*target_hl:.0f}) — "
            f"too slow to revert within refit_freq_bars={refit_freq_bars}"
        )
    elif hl > target_hl:
        flags.append(
            f"⚠ median_half_life {_fmt_float(hl, 1)} bars > target ({target_hl:.0f}) — "
            f"reversion is sluggish vs refit cadence"
        )
    else:
        flags.append(f"✓ median_half_life {_fmt_float(hl, 1)} bars ≤ target ({target_hl:.0f})")

    # --- 4. Realized lifespan sanity ---
    if realised is not None and not (isinstance(realised, float) and math.isnan(realised)):
        ratio = realised / max(refit_freq_bars, 1)
        if ratio < 0.25:
            flags.append(
                f"⚠ median_realized_lifespan {_fmt_float(realised, 0)} bars "
                f"({ratio:.0%} of refit_freq) — baskets retired early too often"
            )
        else:
            flags.append(
                f"ℹ median_realized_lifespan {_fmt_float(realised, 0)} bars "
                f"({ratio:.0%} of refit_freq)"
            )

    # --- 5. Discovery yield (rebalance-level) ---
    n_rebalances = len(log)
    if n_rebalances:
        empty = sum(1 for ev in log if ev.get("n_proposed", 0) == 0)
        if empty / n_rebalances > 0.5:
            flags.append(
                f"⚠ {empty}/{n_rebalances} rebalances proposed 0 baskets — "
                f"find_structures filters may be too strict"
            )

    return flags
