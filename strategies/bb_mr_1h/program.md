# bb_mr_1h

(formerly `rsi2_pullback` — renamed at iter 10 after the RSI(2)
mechanic was abandoned in favour of canonical Bollinger-band MR.)

## Thesis (current — iter 10)
Pivoted from RSI(2) chop-gated MR (iters 1-6) to canonical
**Bollinger-band mean reversion on 1h**. Long when close < lower BB,
short when close > upper BB, exit at midline, gated by EMA(200) trend
direction. Mechanically a different family — the original RSI2 chop
gate was never able to produce positive stitched 24mo P&L.

## Logic (current best — iter 10)
- Decision TF: 1h on BTC + ETH + SOL.
- Bollinger(20, 2.5).
- Trend gate: close > EMA(200) → long-only side; close < EMA(200) →
  short-only side.
- Long: close < lower band; exit when close >= mid OR max_hold (48 bars).
- Short: close > upper band; exit when close <= mid OR max_hold.

## Iter history
| iter | verdict | composite | stitched% | flags / note |
|------|---------|-----------|-----------|-------|
| 1 | KEEP (baseline) | -2.355 | -1.05 | long-only, trend-up gate, EMA200; W2 crash kills it |
| 2 | KEEP | -2.024 | -1.94 | symmetric short side |
| 3 | KEEP | -1.550 | **-68.5** | chop gate; **FAKE KEEP** — Sharpe up, real P&L blown up |
| 4 | REVERT | -2.381 | -66.8 | tighter exits |
| 5 | KEEP | -1.536 | -67.2 | chop-end exit; **FAKE KEEP** (+0.014 composite, stitched still -67%) |
| 6 | REVERT | -2.552 | -1.28 | ATR-expansion filter |
| 7 | REVERT | -3.979 | -60.6 | iter7: 1h RSI2 + EMA200 trend gate, drop chop family |
| 8 | REVERT | -5.953 | **-85.9** | iter8: 1h chop-gated RSI2 — disaster |
| 9 | **KEEP** | **+0.041** | **+4.11** | iter9: switch to BB(20,2) MR on 1h + EMA200 — **first positive stitched** |
| 10 | **KEEP** | **+0.100** | **+9.96** | iter10: bb_k 2.0->2.5, deeper stretch entries (current best) |
| 11 | REVERT | +0.097 | +9.69 | vol-tercile filter — cut trades too far, no improvement |
| 12 | REVERT | -0.478 | -47.77 | drop trend gate — **classic gaming**: OOS Sh +1.38 but stitched -48% |
| 13 | REVERT | +0.002 | +0.23 | bb_k 2.5->2.25 — added trades but lost stitched edge |
| 14 | REVERT | +0.095 | +9.50 | bb_period 20->30 — marginal regression |
| 15 | REVERT | -0.079 | -7.85 | 4h-EMA200 trend filter via resample_higher — same gaming pattern |
| 16 | REVERT | +0.080 | +7.97 | max_hold 48->24 — marginal regression |

## What worked
- **Switch from RSI2 to Bollinger-band MR (iter 9)**: the
  decisive change that produced positive stitched P&L. RSI2 chop-gate
  family on 4h or 1h cannot produce positive 24mo equity — was a
  scoring artifact in iters 3-5.
- **bb_k=2.5 over 2.0 (iter 10)**: deeper stretch entries trade less
  but with higher selectivity; stitched 9.96% vs 4.11%.
- **EMA200 trend-direction gate is essential**: removing it (iter 12)
  collapsed stitched to -48% while OOS Sharpe lifted to +1.38 —
  textbook composite-gaming pattern that the harness flag system
  now catches automatically.

## What's been ruled out (after iters 1-16)
- **RSI(2) chop-gated MR family** on BTC+ETH+SOL on any TF
  (4h, 1h). Always produces negative stitched P&L regardless of
  parameter tuning. Stitched -67% (4h) and -86% (1h-chop) both
  observed.
- **Plain 1h RSI(2) + trend-direction gate** (iter 7): -60% stitched.
  The RSI2 mechanic itself doesn't translate to crypto perp on majors;
  fees + funding + small bounces too small.
- **Vol-tercile filter on top of BB-MR**: cuts trade count
  below the 50-floor without proportionally improving expectancy.
- **Removing trend gate from BB-MR**: collapses to -48% stitched.
- **Wider BB bands (k=2.25 instead of 2.5)**: lower selectivity, no
  improvement.
- **Longer BB period (30 instead of 20)**: marginal regression.
- **4h-EMA200 trend filter at 1h decision TF**: triggers gaming
  pattern (OOS Sharpe up but stitched negative).
- **Shorter max_hold (24h instead of 48h)**: marginal regression.

## Status & recommended next direction
The strategy now has a real but **thin** edge:
- composite +0.10, stitched +9.96% over 24 months, PF 1.16
- But: only 18 OOS trades (small sample penalty), 1/4 windows
  positive (W0 dominates), largest trade = 48% of OOS PnL
  (concentration risk), DSR 0.05.

**Salvageable but fragile.** The BB-MR family found real edge in
5-6 of 12 regime buckets (especially v2-flat at Sharpe +7.6).
The forward-test bet is that the v2-flat regime persists; if
markets pivot to v1-bear or v1-bull-driven, this will bleed.

**Options for user:**
1. **Holdout check now** — see if the iter 10 edge survives 2026 Q1.
   Real risk of overfit-to-OOS given DSR 0.05 and small trade count.
2. **Try further BB-MR refinements**: position sizing by z-depth
   (deeper stretch = larger position), or add a per-symbol BB
   period (BTC at 20, alts at 30+).
3. **Try a regime-aware mix**: BB-MR only when realized-vol z-score
   suggests v2/v3 vol — explicitly skip v1 (low) and v4 (extreme).
4. **Consider this strategy "shipped" at this thin edge level** and
   move attention to other strategies in the pool.
