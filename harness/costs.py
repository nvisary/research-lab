"""Trading cost model.

Two modes:
  - **Static** (default, backwards-compatible): one taker fee + one
    flat slippage in bps. Identical to the original CostModel.
  - **Dynamic**: per-bar, per-symbol slippage built from
      half_spread(t, s) + size_impact(t, s)
    where half_spread comes from estimated bid-ask spreads
    (datafeed/spreads.py) and size_impact scales with order size
    relative to a depth proxy (rolling volume × close).

Static mode keeps existing behaviour and the golden snapshot. Turn on
dynamic mode by setting ``use_dynamic_spread`` and/or
``use_dynamic_slippage`` on the ``CostModel``. Iteration runner does
NOT enable dynamic mode by default; that's a deliberate operator
decision (changing slippage changes the score scale; baselines need
to be re-established).

Tweak here, do not bake into strategies.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CostModel:
    # ----- Static / always-on -----
    taker_fee: float = 0.00055   # Bybit perp taker, fractional (5.5 bps)
    maker_fee: float = 0.0002    # Bybit perp maker (rebate ignored conservatively)
    slippage_bps: float = 1.0    # legacy flat slippage (used when dynamic off)
    apply_funding: bool = True   # subtract funding-rate cashflows from equity

    # ----- Dynamic costs (off by default) -----
    use_dynamic_spread: bool = False
    """If True, replace flat slippage with per-bar half-spread loaded
    from data/meta/spreads/<sym>/. Falls back to ``slippage_bps`` flat
    on symbols/months without spread parquets."""

    use_dynamic_slippage: bool = False
    """If True, ADD a size-impact component on top of the spread
    component: ``size_k * order_size_usd / depth_proxy_usd``."""

    spread_to_slippage_ratio: float = 0.5
    """Taker pays roughly half the spread to cross. Roll's estimator
    gives the full spread; this multiplier converts it to slippage."""

    slippage_size_k: float = 0.5
    """Coefficient on size impact. Order of size = 100% of bar's
    rolling volume × this k = added bps. Rough; calibrate per venue."""

    size_impact_window: int = 60
    """Bars used in the rolling depth proxy (mean of volume × close).
    60 at 1m TF = 1h depth; at 4h TF = 240h ≈ 10d depth."""

    size_impact_cap_bps: float = 100.0
    """Cap on size impact contribution (bps). Stops a single absurdly
    large order in a thin bar from blowing the model. 100 bps = 1%."""

    min_slippage_bps: float = 0.5
    """Floor on total slippage. Even on the most liquid bars and
    smallest sizes, a market-taker pays something."""

    @property
    def total_one_way(self) -> float:
        return self.taker_fee + self.slippage_bps * 1e-4

    @property
    def is_dynamic(self) -> bool:
        return self.use_dynamic_spread or self.use_dynamic_slippage


DEFAULT = CostModel()


# --------------------------------------------------------------------------- #
# Slippage matrix construction
# --------------------------------------------------------------------------- #
def build_slippage_matrix(prices: pd.DataFrame, volumes: pd.DataFrame | None,
                          target_pos: pd.DataFrame, init_cash: float,
                          costs: CostModel) -> pd.DataFrame | float:
    """Per-bar, per-symbol slippage rate (FRACTIONAL, e.g. 0.0005 = 5 bps).

    Returns either a scalar fraction (static mode, for vectorbt
    broadcast) or a DataFrame with the same shape as ``prices`` (dynamic
    mode, accepted by ``vbt.Portfolio.from_orders(slippage=...)``).

    Components in dynamic mode:
      1. half_spread(t, s) from saved spread series. Hourly bucket → bar
         grid via ffill. Symbols without saved spreads use ``slippage_bps``
         flat as the spread component.
      2. size_impact(t, s) = size_k * order_size_$ / depth_$. Order size
         is approximated as ``init_cash / n_symbols * |Δtarget_pos|`` —
         a constant proxy that ignores PnL-driven equity changes; good
         enough for rank/penalty effects.
      3. Sum of the two, floored at ``min_slippage_bps``, with the size
         component clipped at ``size_impact_cap_bps``.
    """
    # Static mode: scalar broadcast preserves vectorbt's fast path.
    if not costs.is_dynamic:
        return costs.slippage_bps * 1e-4

    # Dynamic mode: import here to avoid a hard dependency on the
    # spreads parquets when the static path is taken.
    from datafeed.spreads import load_spread_series, reindex_spreads_to_bars

    n = max(prices.shape[1], 1)
    matrix_bps = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)

    # --- 1. Spread component ---
    for sym in prices.columns:
        if costs.use_dynamic_spread:
            try:
                spread_df = load_spread_series(sym, prices.index[0], prices.index[-1])
            except Exception:
                spread_df = pd.DataFrame()
            spread_bps_series = reindex_spreads_to_bars(
                spread_df, prices.index, fallback_bps=costs.slippage_bps,
            )
            half_spread_bps = spread_bps_series * costs.spread_to_slippage_ratio
            matrix_bps[sym] = half_spread_bps.values
        else:
            matrix_bps[sym] = costs.slippage_bps

    # --- 2. Size-impact component ---
    if costs.use_dynamic_slippage and volumes is not None:
        # Depth proxy: rolling mean of (volume × close), scaled to a
        # ~window-long $-volume estimate. We use the mean (per-bar
        # average $) × window so the units are "$ traded across the
        # window" — a stand-in for visible top-of-book depth that
        # could absorb a fraction-of-window-volume order without much
        # impact. Rough; intentionally so.
        notional_per_bar = volumes.reindex_like(prices) * prices
        depth_usd = (
            notional_per_bar
            .rolling(costs.size_impact_window, min_periods=max(costs.size_impact_window // 6, 5))
            .mean()
            * costs.size_impact_window
        )
        depth_usd = depth_usd.replace(0, np.nan).ffill().bfill()

        # Approximate per-bar order size in $. ``init_cash / n`` is the
        # nominal per-symbol slot; |Δtarget| is the rebalance fraction.
        # Doesn't model post-PnL equity drift — good enough for cost
        # ranking, intentionally biased slightly low (better understated
        # size impact than overstated total slippage stacking).
        position_change = target_pos.diff().abs().fillna(target_pos.abs())
        order_size_usd = position_change * (init_cash / n)

        size_bps = costs.slippage_size_k * (order_size_usd / depth_usd) * 1e4
        size_bps = size_bps.clip(upper=costs.size_impact_cap_bps).fillna(0.0)
        matrix_bps = matrix_bps + size_bps

    # --- 3. Floor & convert to fraction ---
    matrix_bps = matrix_bps.clip(lower=costs.min_slippage_bps)
    return matrix_bps * 1e-4
