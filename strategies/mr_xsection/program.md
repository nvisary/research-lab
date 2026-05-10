# mr_xsection — cross-sectional mean-reversion

## Baseline

Each 4h bar, rank the 10-major basket by 14-day trailing return.
Long the bottom 30% (worst recent performers), short the top 30%
(best recent performers). Position rebalances every bar; positions
shift continuously as ranks change.

```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "4h"
lookback        = 84   (14d at 4h)
long_quantile   = 0.3
short_quantile  = 0.3
long_only       = 0
```

## Hypothesis

Within a basket of correlated majors, short-term return dispersion
contains a mean-reverting component. The recent worst performer is
more likely to outperform the recent best over the next N bars
than continue lagging. Bet captures this regression-to-basket-mean.

## Why this slot in the quadrant

Cross-sectional MR. Rank-based, so signals are RELATIVE within the
basket — fires regardless of absolute direction. Naturally orthogonal
to mr_zscore (which fires on absolute per-asset extremes).

In a uniformly trending basket, mr_xsection has clear long/short
signals (winners vs losers) while mr_zscore has none (no absolute
extremes). In a sideways but high-vol regime, mr_zscore fires often
while mr_xsection has small dispersion → low trade volume.

## Open questions

- Lookback sweep — 7d / 14d / 30d
- Quantile sweep — 0.2 / 0.3 / 0.4 (controls breadth & turnover)
- Holding period: continuous rebalance vs N-bar hold (lower turnover)
- Skip latest 1d (anti-microstructure noise) — classic 12-1 month variant
- Volatility-normalised returns (z-rank) instead of raw return rank
