# vwap_zscore_reversion

## Thesis
Z-score deviation from rolling 48-bar VWAP mean-reverts, but only in
non-trending regimes. Trend-direction gate (original) was wrong; what works
is a chop-only gate (|4h slope| small).

## Logic (current best)
- Decision TF: 1h on BTC+ETH.
- VWAP and σ over 48 hourly bars; z = (close − vwap) / σ.
- Chop gate: |4h EMA(100) slope over 12 bars| < 3%.
- Long: z < -1.8 in chop; exit z > -0.3, 36-bar timeout.
- Short: mirror.

## Iter history
| iter | verdict | composite | note |
|------|---------|-----------|------|
| 1 | KEEP (baseline) | -2.98 | with-trend gate — wrong direction, lossy bull/bear buckets |
| 2 | KEEP | **+0.234** | chop-only gate (|4h slope|<3%) — flat-regime alpha exposed |

## Ruled out
- Trend-direction gate for VWAP MR — strong trends overrun reversion.

## Caveats
- W3 dominates (71% of |sharpe|) — selection bias risk.
- Stitched 24-mo return -58.7%, but per-window OOS aggregates positive — calendar bias warning, treat composite as upper bound.
- Real alpha is in v1-v4 flat buckets (Sharpe +13 to +17 there).
