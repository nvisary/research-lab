# rsi2_pullback

## Thesis
RSI(2) mean reversion in non-trending regimes only. Trend-direction gate was
the wrong filter (got crushed in W2 bear crash); chop-only gate is what works.

## Logic (current best — iter 6)
- Decision TF: 4h on BTC+ETH (SOL dropped — too volatile, dragged W2).
- RSI(2). Chop gate: |daily EMA(100) slope over 12 bars| < 2.5%.
- Long: RSI2 < 15 in chop; exit RSI2 > 60, 24-bar timeout.
- Short: RSI2 > 85 in chop; mirror.

## Iter history
| iter | verdict | composite | note |
|------|---------|-----------|------|
| 1 | KEEP (baseline) | -4.71 | 1h, with-trend gate, RSI2<10 — fee-dominated |
| 2 | KEEP | -2.00 | move to 4h, fewer trades |
| 3 | REVERT | -2.90 | long-only — n_trades collapsed |
| 4 | KEEP | -1.17 | switch to chop-only gate, |daily slope|<5% |
| 5 | KEEP | -1.15 | tighten chop slope 0.05→0.025 |
| 6 | KEEP | **+0.086** | drop SOL — variance from alt killed W2 |

## What's been ruled out
- 1h decision frequency (costs > edge).
- Trend-direction gate (trending regimes overrun MR).
- Long-only (drops n_trades below penalty floor).
- SOL in universe (too volatile, dominates DD).
