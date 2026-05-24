"""harness_statarb — statistical arbitrage backtest mode.

Parallel "mode" alongside the directional-strategy harness. Reuses
`harness.backtest.run_split` as the bottom layer for fees / funding /
equity, but adds:

- A two-stage strategy contract: `find_structures` (basket discovery on
  train slice) + `trade_basket` (per-bar position on OOS slice).
- A two-level backtest loop: rolling basket discovery + lifecycle
  management + aggregation of per-basket positions to a per-symbol
  position panel passed down to `harness.backtest`.
- Stat-arb composite (Sharpe − 0.5·MaxDD − survival_penalty −
  half_life_penalty − low_trades/tip penalties).
- A separate lookahead audit: basket structures must depend only on
  the train slice (permutation test on post-fit bars).

The existing `harness/` package is **not** modified; trend/MR strategies
continue to work unchanged.
"""
from __future__ import annotations

from harness_statarb.structures import (
    Basket,
    adf_pvalue,
    engle_granger,
    hedge_ratio_stability,
    johansen,
    ou_half_life,
    pca_decompose,
)

__all__ = [
    "Basket",
    "adf_pvalue",
    "engle_granger",
    "hedge_ratio_stability",
    "johansen",
    "ou_half_life",
    "pca_decompose",
]
