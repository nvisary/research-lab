# zret_mr_alts — research log

## Slot
Per-asset mean-reversion (MR), same universe as `zscore_mr_alts`. Sibling
strategy: signal architecture differs (return-based z, not price-based).

## Baseline thesis
Inherited best from `zscore_mr_alts` (iter 6: composite -0.13, OOS Sharpe
+0.52, PF 1.04). Single structural change: replace price-z with return-z.

Why: price-z catches both blow-off events AND slow trend grinds (in a smooth
uptrend, `(close - SMA)/std` stays elevated for a long time because price is
persistently at the top of recent range — but that's *trend*, not
*overextension*, so the fade just bleeds). Return-z is closer to zero in
steady trends and only spikes on shocks — the events that actually
mean-revert.

Definition: `z = (r_N - SMA(r_N)) / std(r_N)` where `r_N = log(close) -
log(close[-N])` is the N-bar log return and the moving stats use the same N
window applied to `r_N` itself.

## Universe
Same 24 mid-cap alts as zscore_mr_alts (full 2024-2026 coverage).

## Filters kept from zscore_mr_alts best (iter 6 of that strategy)
- 4h trend-regime gate, q=0.3 (only fade in flat regime)
- Vol-floor, q=0.2 (skip entries when ATR% in bottom 20%)
- Symmetric L/S
- z_window=96 (24h)

## Planned iteration directions
1. **Baseline** — drop-in replacement of signal; everything else identical.
   Direct A/B vs zscore_mr_alts iter 6.
2. If baseline beats zscore: z_window sweep on return-z (might want different
   horizon than price-z optimum).
3. Asymmetric exit/entry thresholds.
4. Volume confirmation at entry (independently of signal change).
5. Cost-aware thresholds — wider when realized cost-to-edge is poor.

## Iteration log

| # | Verdict | Composite | OOS Sharpe | MaxDD | n_trades | TiP% | Stitched | PF | DSR | Note |
|---|---------|-----------|------------|-------|----------|------|----------|----|----|------|
| 1 | BASELINE | -1.42 | +0.15 | 13% | 575 | 88% | -2.8% | 1.05 | 0.57 | return-z replaces price-z; z_window=96, regime_q=0.3, vol_floor=0.2, exit_k=0.5 — same filters as zscore iter 6 |
| 2 | REVERT | -2.92 | -0.48 | 14% | 910 | 85% | -66% | 1.01 | 0.30 | z_window 96→48 (12h) — noisier shorter window, more trades, costs eat signal |
| 3 | REVERT | -1.61 | -0.69 | 15% | 317 | 90% | -48% | 0.89 | 0.24 | z_window 96→192 (48h) — too long; regime decomp regresses to price-z pattern (flat-buckets +9, bear -10) |
| 4 | KEEP | -1.23 | +0.65 | 8% | 608 | 74% | -2.7% | 1.17 | 0.60 | exit_k 0.5→1.0 — **breakthrough**: partial-reversion exit cuts loss tails; first PF>1 in project |
| 5 | REVERT | -1.70 | +0.28 | 9% | 671 | 53% | -16% | 1.13 | 0.44 | exit_k 1.0→1.5 — past peak; cuts wins too |
| 6 | REVERT | -1.33 | +0.22 | 7% | 463 | 67% | **+0.56%** | 1.10 | 0.40 | regime_q 0.3→0.2 — composite worse, but FIRST positive stitched equity; gaming-vs-equity mismatch |
| 7 | KEEP | -0.97 | +0.73 | 8% | 506 | 68% | -0.6% | 1.18 | 0.55 | vol_floor 0.2→0.35 — cleaner v1-bear cleanup, all bull-buckets near zero |
| 8 | KEEP | -0.56 | +1.05 | 7% | 397 | 59% | **+1.22%** | 1.24 | 0.61 | vol_floor 0.35→0.5 — OOS Sharpe crosses 1.0 first time, positive stitched, DSR ties zscore peak |
| 9 | **KEEP** | **+0.18** | **+1.64** | 5% | 293 | 51% | **+8.56%** | 1.35 | **0.72** | vol_floor 0.5→0.65 — **first positive composite ever**; ✓ multi-regime (6/12 healthy); BHY-haircut Sharpe 0.53 (statistically meaningful) |
| 10 | REVERT | -0.16 | +1.29 | 5% | 216 | 43% | **+15.24%** | 1.10 | 0.56 | vol_floor 0.65→0.75 — composite past peak, but stitched +15% (best ever!) and 14g/10r monthly — same composite-vs-equity mismatch |

**Current best**: iter 9.
**Best stitched**: iter 10 (+15.24% / 24mo) — but REVERTed.

## What worked
1. **return-z over price-z** structurally better — even baseline (iter 1) had
   stitched -2.8% vs price-z best of -47.4%.
2. **exit_k=1.0** (partial-reversion exit) was the breakthrough — cuts the
   loss-tail asymmetry that capped PF at 1.0 in zscore_mr_alts.
3. **Aggressive vol_floor (0.65)** — clean signal by ignoring own-low-vol
   periods entirely. v1-bear leak remains but smaller in absolute terms.
4. Regime gate at q=0.3 stayed (didn't need tighter; iter 6 q=0.2 was REVERT).

## What didn't work
- z_window sweep — 96 is concave optimum, both shorter and longer worse.
- exit_k=1.5 — past peak.
- regime_q=0.2 — composite hurt despite stitched improving.
- vol_floor=0.75 — same composite-vs-equity mismatch.

## Open observations / risks
- **W0 (early-2024) is persistently the worst window** in every iteration.
  Some structural feature of Q1 2024 doesn't suit this signal. Did not
  investigate — fixing it specifically would risk calendar overfit.
- **The composite consistently undervalues high-stitched, lower-Sharpe
  variants** (iter 6, iter 10). The optimizer's "best" is iter 9; the
  trader's best may be iter 10. Worth running iter 10 params manually
  on holdout for the user's own judgment.
- **Fat-tail dependent** flag fired most iters. Several individual trades
  account for >50% of total PnL. Real edge but with concentration risk.
- **v1-bear bucket (low-vol bear)** is the irreducible leak. Vol-floor
  shrinks total trade count in that regime; bull/flat buckets compensate.

## Recommendation
Stop iterating on this hypothesis-family. iter 9 is the legitimate
optimizer-best. iter 10 is the trader's-eye-best. DSR 0.72 is the
highest in the project; further tuning is more likely to be selection
bias than real improvement.

Next moves (for the user):
1. **Run holdout** (`runner.holdout`) on iter 9 to see if 2026-Q1 confirms.
2. Consider iter 10 as a separate "preferred for live" branch.
3. If both holdout well, the strategy is shippable. If iter 9 holdout
   collapses, the +1.64 OOS Sharpe was selection bias.

## Additional iterations after iter 10 (push toward TRUST)

| # | Verdict | Composite | Sharpe | DSR | Train↔OOS | BHY | Note |
|---|---|---|---|---|---|---|---|
| 11 | KEEP | +0.26 | 1.45 | 0.59 | **3/4** ✓ | 0.11 | regime_q 0.3→0.2 — flipped W1/W3 train sign positive; train↔OOS gate cleared |
| 12 | REVERT | +0.25 | 1.16 | 0.46 | 3/4 | 0.0 | entry_k 2.0→2.5 — fewer trades → lower Sharpe, BHY drops to 0 |
| 13 | REVERT | +0.01 | 1.21 | 0.52 | 2/4 | 0.0 | regime_lookback 30→60 — destabilized W2, agreement worse |
| 14 | REVERT | -1.40 | 0.21 | 0.19 | — | 0.0 | entry_k 2.0→1.5 — opposite direction, also worse; entry_k=2.0 is concave optimum |

**Final best**: iter 11 (entry_k=2.0, regime_q=0.2, vol_floor=0.65, exit_k=1.0, z_window=96).

## Holdout 2026-01..2026-05 — strategy failed final exam

```
composite_holdout:  -0.56   (vs train+val best +0.26)
sharpe:             -0.52   (vs train+val OOS +1.45, sign flip)
max_dd:              7.85%  (vs train+val OOS 3.6%, 2.2x worse)
n_trades:            750    (over 4 months — robust trade count)
```

The in-sample +1.45 Sharpe was selection bias, not edge. Holdout confirms.

**What the warning systems said, and were right about:**
- DSR 0.59 (deflated for 11 trials) — significantly below the 0.72 peak (iter 9),
  signaling the marginal improvement at iter 11 was selection-bias accumulation
- BHY-haircut Sharpe 0.11 — after multi-testing correction, the Sharpe is
  near zero. The dashboard explicitly flagged NOISE-FIT.
- Train↔OOS 3/4 — passed the gate barely, but W1 disagreement (train +0.05,
  OOS +3.79) was a sign that the OOS was lucky, not robust.

The system did its job — flagged this as noise-fit before holdout, and holdout
confirmed.

## Final conclusion

**Hypothesis: 15m MR (any signal variant) on 24 mid-cap alt perps has no
robust edge.** Three families tested:
1. price-z (zscore_mr_alts): best PF 1.04, holdout not run (already too weak)
2. cross-sectional (xs_mr_alts): best PF 0.95, never positive
3. return-z (zret_mr_alts): best in-sample PF 1.32, **holdout PF < 1** (failed)

The structural issue: 5.5bp taker × 2 = 11bp round-trip cost is too high a wall
for 15m MR signals on these names. Crypto mid-caps don't mean-revert reliably
enough at this timeframe to overcome it.

**Not shippable.** Move to a different hypothesis-family.
