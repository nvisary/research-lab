# donchian_atr_trend

## Thesis (current)
In non-trending regimes, donchian-N pierces are exhaustion points, not
breakouts → fade them back to channel mid. Restrict to FIRST pierce in a
cooldown window so continuation-pierces during real trends don't get faded.
Stack a Bollinger-bandwidth-percentile gate (compression regime) on top of
the daily-slope chop gate to isolate the flat-regime edge.

## Logic (current best — iter 14 at q_max=0.25, but iter 13 at q_max=0.35 preferred for robustness)
- Decision TF: 4h on BTC+ETH.
- Donchian-20 high/low. Channel mid = (upper+lower)/2.
- Daily chop gate: |daily EMA(100) slope over 12 bars| < 5%.
- BB-width compression gate: BB(20) bandwidth must be in lowest q_max
  percentile of trailing 180 bars (q_max=0.25 iter 14 best, 0.35 iter 13).
- Pierce-cooldown: no upper pierce in prior 10 bars (mirror for lower).
- Fade short: prior-bar high pierced upper, current close back inside.
- Fade long: mirror at lower.
- Exit: close back at mid, ATR(14)·2.5 stop, 24-bar timeout.

## Iter history
| iter | verdict | composite | stitched | flag | note |
|------|---------|-----------|----------|------|------|
| 1 | KEEP (baseline v2) | -1.4497 | -49.1% | | scoring update reset best |
| 2 | KEEP | -0.1492 | -14.9% | | pierce-cooldown 10 first-pierce-only |
| 3 | REVERT | -0.2844 | -28.4% | | + 4h EMA50 slope chop gate (over-restrictive) |
| 4 | REVERT | -0.3256 | -10.4% | | directional veto by close vs 4h EMA200 |
| 5 | REVERT | -0.5344 | -18.5% | | tighten ATR stop 2.5→1.5 |
| 6 | REVERT | -0.5400 | -6.7% | | drop short side (bear collapse) |
| 7 | REVERT | -0.3297 | -12.8% | flat=+5,bull/bear leak | replace daily-slope with 4h ADX<20 |
| 8 | REVERT | -0.3826 | -32.7% | profit_factor 0.72 | stack realized-vol-percentile gate |
| 9 | REVERT | -1.7186 | -19.1% | neg OOS Sharpe | swap symbols BTC/ETH → XRP/LTC |
| 10 | REVERT | -1.3620 | -30.1% | profit_factor 0.68 | donchian-mid slope as chop gate |
| 11 | REVERT | -1.0809 | -19.8% | 6 neg months | shorten max_hold 24→12 |
| 12 | **KEEP** | **-0.0805** | **-8.1%** | all 4 OOS+, DSR 0.70 | stack BB-width q_max=0.50 compression gate |
| 13 | **KEEP** | **+0.3146** | **+5.6%** | PF 1.10, stitched flip | tighten BB q_max 0.50 → 0.35 |
| 14 | KEEP (suspect) | +0.3829 | +4.9% | tip 18.1%<20% gaming flag | tighten BB q_max 0.35 → 0.25 |

## Robust best = iter 13 (q_max=0.35)
- composite +0.3146, DSR 0.63, all 4 windows positive (or near it).
- **Stitched +5.59% over 24 months** — first positive stitched ever for this strategy.
- tip 28.5%, 13 OOS trades, PF 1.10.
- profit_factor 2.11 on WF aggregate, expectancy +25 USD/trade.

## Iter 14 caveat
The harness KEPT iter 14 (q_max=0.25) because composite rose +0.07,
but the tip-flag is active (18.1% < 20%) — CLAUDE.md gaming guard
triggered. Only 9 OOS trades; largest trade = 110% of total PnL.
Stitched dropped slightly (+5.6 → +4.9) and DSR dropped (0.63 → 0.52).
**Treat iter 14 as overfit on the BB-q dimension; the real edge is iter 13.**

## What's been ruled out (this batch, iters 7-14)
- Replacing the daily-slope chop gate with a single sharper classifier
  (ADX, donchian-mid-slope, BB-only) — each individually fails because
  the directional-bucket leakage requires multi-factor narrowing, not
  a single drop-in replacement.
- Stacking only realized-vol-percentile on slope-chop (iter 8) — keeps
  too many high-vol bull bars where fades still bleed.
- Symbol swap to XRP+LTC (iter 9) — same regime topology, no improvement.
- Tightening time-stop (max_hold 24→12, iter 11) — same regime leakage,
  worse trade-count economics.
- Tightening ATR stop below 2.5x (iter 5, prior batch).
- Long-only or short-only directional sub-mode (iter 6, prior batch).

## What worked (this batch)
- **BB-width-percentile compression gate STACKED on top of daily-slope
  chop gate (iter 12).** The two filters together narrow to bars where
  BOTH the daily trend is flat AND the local volatility structure is
  compressed — this is a sharper "true chop" classifier than either
  alone. Stitched flipped from -15% to +5.6% with q_max=0.35.

## Diagnostic regularity (still holds across 14 iters)
Regime decomposition shows: flat-trend buckets +5 to +10 Sharpe,
bull/bear buckets -3 to -8 Sharpe. Iter 12-13 didn't fix the bucket
shape — it just made the strategy spend more bars in flat. The edge
in directional buckets remains negative; it's now just better hidden
because we're in cash more during those buckets.

## Recommended next direction
1. **Robustness probe**: test iter 13 (q_max=0.35) holdout (USER trigger).
   The single-largest-trade flag (85-110% of total) means fat-tail
   dependence is high; holdout will reveal whether the +5.6% stitched
   is a real shape or one lucky compression episode.
2. **Position sizing in chop**: with the compression gate working,
   try scaling position size proportional to (1 - bb_rank) — even
   bigger size in the tightest compression. Could lift expectancy
   without adding new trades.
3. **Alternative compression measures**: Keltner-vs-BB squeeze
   (TTM-squeeze classic) — Keltner channels stay inside BB only
   during compression. A binary squeeze flag may be more robust
   than BB rank.
4. **Probe the bull/bear bucket leakage directly**: instead of just
   gating it out, exit positions instantly if the trade enters a
   newly bull/bear regime mid-hold (regime-change kill switch).
