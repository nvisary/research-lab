# Cross-sectional momentum — research strategy

## Hypothesis

Within a basket of crypto perp futures, **relative strength is a stronger
signal than absolute trend on any single asset**. By long-decile / short-decile
ranking, we hedge market beta out of the PnL — the long leg's BTC beta is
cancelled by the short leg's BTC beta, and what's left is "did the symbols I
picked outperform the symbols I avoided?"

This is the textbook recipe (Asness/Moskowitz/Pedersen 2013, "Value and
Momentum Everywhere") adapted to crypto.

## Why this strategy specifically

The previous research session on `strategies/sma_cross` ended with a champion
whose mean OOS Sharpe of +2.76 was indistinguishable from passive buy-and-hold
BTC (alpha = -0.05). Long-only single-asset trend on a strongly-trending
universe captures beta, not edge. **Cross-sectional momentum is structurally
beta-neutral** — the answer to "does this strategy add anything over b&h" is
unambiguous because its market exposure is ~0.

## Data context

We have klines for 173 USDT-perp symbols launched before 2024-01-01 (the
"sturdy" universe — no mid-period delisting), 1m bars covering 2024-01 to
2026-04. Funding parquets for all of them, downloaded at the start of this
session.

## Initial design (what the baseline looks like)

- Universe: top N most-liquid USDT perps from the cached symbol pool
- Decision TF: **1d** (slower than the trend pilots — ranking is naturally
  weekly-monthly horizon)
- Score: 30-day return per symbol
- Long top decile (e.g. top 5 if N=30), short bottom decile
- Equal weight inside each leg
- Rebalance every bar (1d)

## Open questions for iteration

1. What N (universe size) maximizes alpha vs noise?
2. What lookback for the momentum score? 30d is a starting point;
   shorter (7d) ≈ short-term reversal, longer (60-90d) ≈ slower trend
3. Decile vs quintile — narrower selection = higher conviction but
   worse diversification
4. Position sizing: equal-weight or vol-target the basket as a whole?
5. Funding-aware: scale down legs that pay heavy funding cost
6. Skip-most-recent-week (Asness "skip-month") — reverses tend to
   punish raw momentum at very short horizons

## What's been ruled out
(populate as iterations refute hypotheses)
