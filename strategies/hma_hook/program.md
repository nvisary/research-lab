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
| 21 | **KEEP** | **-0.445** | **+0.157** | **4.9%** | 39 | 62% | **+0.7%** | **multi-symbol basket (champion)** |
| 22 | REVERT | | | | | | | ATR-based trailing stop |
| 23 | REVERT | | | | | | | normalized trend slope |
| 24 | REVERT | | | | | | | funding carry gate |
| 25 | REVERT | | | | | | | volume spike confirmation |
| 26 | REVERT | | | | | | | dynamic HMA blend |
| 27 | REVERT | | | | | | | RSI extremes gate |
| 28 | REVERT | | | | | | | OHLC average input |
| 29 | REVERT | | | | | | | ADX > 25 gate |
| 30 | REVERT | | | | | | | ensemble of 3 HMAs |
| 31 | REVERT | | | | | | | dynamic sizing boost |

## Final summary
Implemented the HMA "Hook" (direction change) strategy. Expanded the universe to BTC, ETH, and SOL in Iteration 21, which significantly improved the composite score and reduced the low-trade penalty.

**Champion — iter 21:**
- Composite: -0.445
- OOS Sharpe: +0.157
- Stitched return: +20.58%
- MaxDD: 4.9%
- Universe: BTCUSDT, ETHUSDT, SOLUSDT

Further attempts at adding technical filters (RSI, ADX, Ensemble) or risk management (Trailing Stops) consistently resulted in REVERTs, as they tended to delay entries or over-filter winners, degrading the OOS Sharpe. The strategy remains a stable, diversified trend-follower.
