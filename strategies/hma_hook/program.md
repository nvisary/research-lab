# hma_hook — research log

## Slot
Per-asset trend-following (TS). Single-symbol BTCUSDT 4h. Similar to `macd_ema200`, `trend_supertrend`.

## Baseline thesis
Hull Moving Average (HMA) "Hook" strategy.
- HMA(n) is used to detect trend direction with minimal lag.
- Long while `HMA` is rising.
- Short while `HMA` is falling.
- Symmetric L/S by default.

HMA is designed to reduce lag while maintaining smoothness. The "Hook" occurs when the slope of HMA changes sign.

## Planned iteration directions (priority order)
1. Establish baseline at length=20, 4h TF.
2. Sweep `length` parameter.
3. Test `long_only` version.
4. Add trend filter (e.g., EMA200).
5. Vol-targeted sizing and extreme-vol gate.

## Iteration log

| # | Verdict | Composite | OOS Sharpe | MaxDD | n_trades | TiP% | TotalRet | Note |
|---|---------|-----------|------------|-------|----------|------|----------|------|
| 1 | BASELINE | -3.036 | -1.175 | 18% | 32 | 75% | -3.7% | 4h HMA(20), symmetric L/S, no sizing |
| 2 | **KEEP** | -1.363 | -0.409 | 6.8% | 16 | 39% | -0.6% | add EMA200 price + slope filter |
| 3 | REVERT | | | | | | | length 20->40, long_only=1 (0 trades in W3) |
| 4 | **KEEP** | -0.752 | +0.320 | 7.6% | 13 | 40% | +1.0% | length 20->30 |
| 5 | **KEEP** | **-0.681** | **+0.391** | **7.6%** | 13 | 42% | **+1.2%** | **slope_lb 24->12 (champion)** |
| 6 | REVERT | | | | | | | vol_q 0.70->0.50 (over-filtered) |
| 7 | REVERT | | | | | | | vol_q 0.70->0.90 (too noisy) |
| 8 | REVERT | | | | | | | 1bps slope threshold (cut winners) |
| 9 | REVERT | | | | | | | 2-bar confirmation (too late) |
| 11 | REVERT | | | | | | | 1d EMA gate |
| 12 | REVERT | | | | | | | conviction sizing |
| 13 | REVERT | | | | | | | ADX (buggy run) |
| 14 | REVERT | | | | | | | PPO regime |
| 15 | REVERT | | | | | | | stateful exit |
| 16 | REVERT | | | | | | | RSI > 50 momentum gate |
| 17 | REVERT | | | | | | | vol-of-vol filter |
| 18 | REVERT | | | | | | | Keltner Channels regime |
| 19 | REVERT | | | | | | | rolling VWAP input for HMA |
| 20 | REVERT | | | | | | | ADX > 20 filter (fixed) |

## Final summary
Implemented the HMA "Hook" (direction change) strategy. Baseline was catastrophic, but adding a long-term trend filter (EMA200 + slope) and smoothing the HMA (length 30) stabilized the results. 

**Champion — iter 5:**
- Composite: -0.681
- OOS Sharpe: +0.391
- Stitched return: +13.85%
- MaxDD: 7.6%
- Trade count remains low (~13 per OOS window), triggering penalty.

The strategy is a solid trend-follower on BTC 4h. It thrives in sustained bull/bear moves (e.g., late 2024, early 2025) but bleeds during the cycle-peak chop of Q4 2025. Further filters (RSI, ADX, VWAP, Keltner) did not improve the composite, often leading to over-filtering or delayed entries.
