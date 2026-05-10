# mom_xsection — cross-sectional momentum

## Baseline

Each 1d bar, rank the 10-major basket by 60-day trailing return.
Long the top 30% (best recent performers), short the bottom 30%
(worst). Continuous rebalance — positions shift as ranks change.

```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "1d"
lookback        = 60
long_quantile   = 0.3
short_quantile  = 0.3
long_only       = 0
```

## Hypothesis

Within a basket of correlated majors, recent-strength persists over
horizons of weeks to months. The recent best performer continues to
outperform the basket centroid; the recent worst continues to lag.
Captures the cross-sectional momentum factor (Jegadeesh-Titman 1993).

## Why this slot in the quadrant

Cross-sectional momentum. Rank-based, RELATIVE within the basket.
Orthogonal to mom_tsmom (TSM fires on absolute direction, CSM fires
on dispersion).

In a uniform trend, TSM and CSM both work but pick different
opportunities (TSM rides everyone, CSM picks the strongest relatively).
In a chop where some symbols trend and others don't, CSM extracts
that dispersion alpha that TSM averages away.

## Open questions

- Lookback sweep — 14 / 30 / 60 / 90 / 180 days
- Skip latest week (1y minus 1w — classic 12-1 month variant)
- Quantile sweep — 0.2 / 0.3 / 0.4
- Volatility-normalised ranking (z-score of return, not raw)
- Holding period: continuous rebalance vs N-day hold
- Long-only variant (avoids short funding drag) — but kills the
  market-neutral property
