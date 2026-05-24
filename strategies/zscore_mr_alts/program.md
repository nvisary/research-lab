# zscore_mr_alts — research log

## Slot
Cross-sectional mean-reversion (XS-MR) on mid-cap perp alt basket.
Sibling to any future single-asset MR (RSI2, BB-fade) and to XS-momentum (the
opposite-sign cousin).

## Baseline thesis
Mid-cap alts (excluding BTC/ETH/SOL/BNB/XRP) chop harder than majors and revert
to a short-window mean on 15m more reliably than they trend.

- 15m bars, 24h rolling z-score `(close - SMA(96)) / std(96)`.
- Enter long at `z < -2`, short at `z > +2`, exit when `|z| < 0.5`.
- State machine: one position per symbol, hold between thresholds.
- Cross-sectional equal-weight: each symbol = 1/n of equity, gross ≤ 100%.

## Universe (24 mid-cap perps)
DOGE, AVAX, LINK, DOT, TRX, BCH, NEAR, ATOM, XLM, OP, INJ, SUI, TIA, SEI, UNI,
FIL, HBAR, ICP, LDO, CRV, SAND, AXS, IMX, ETC.

## Iteration log (session 2 — May 2026, new scoring with stitched-floor)

Started from iter 1 baseline (composite -0.605, mean OOS Sharpe +0.52, stitched
**-47.4%**). Session 1's old iter 2-10 history (in earlier program.md) was
under the old composite formula and is now superseded — they're listed below
as priors for context.

| # | Verdict | Composite | OOS Sharpe | Stitched% | MaxDD | n_trades | TiP% | Note |
|---|---------|-----------|------------|-----------|-------|----------|------|------|
| 1 | BASELINE | -0.605 | +0.52 | **-47.4%** | 12% | 639 | 90% | regime_q=0.3, vol_floor_q=0.2, entry_k=2.0 |
| 2 | REVERT | -13.67 | -10.3 | -100% | 59% | 5113 | 100% | XS-ranking (long bottom-4 z, short top-4 z) — catastrophic turnover, every bucket -ve |
| 3 | REVERT | -6.14 | -3.82 | -56% | 15% | 862 | 32% | return-based z (4-bar logret excursion vs 24h dist) — MR mechanism breaks on log-ret |
| 4 | REVERT | -1.89 | +0.09 | -51% | 12% | 650 | 73% | + 8h time-stop — heals regime decomp (5 healthy buckets) but stitched worse |
| 5 | **KEEP** | -0.513 | +1.00 | -31.9% | 10% | 437 | 79% | vol_floor_q 0.2→0.5 (above-median ATR only) — +15.5pp stitched, 4 healthy flat buckets glow |
| 6 | **KEEP** | -0.397 | +0.65 | -20.2% | 8% | 313 | 72% | regime_quantile 0.3→0.2 (tighter flat-only) — +11.7pp stitched, bear/bull buckets less lossy |
| 7 | REVERT | -0.66 | +0.21 | -21.7% | 8% | 200 | 61% | entry_k 2.0→2.5 — fewer trades, hit-rate up but stitched flat |
| 8 | REVERT | -2.80 | -0.57 | -24.5% | 8% | 328 | 61% | + 3·ATR price-stop — stop crystallizes losses that would have reverted (same lesson as old z-stop) |
| 9 | **KEEP** | -0.337 | +0.43 | **-19.6%** | 7% | 245 | 65% | regime_quantile 0.2→0.15 — best stitched of session, 12/12 monthly green/red |
| 10 | REVERT | -0.92 | -0.27 | -18.9% | 7% | 166 | 55% | regime_quantile 0.15→0.10 — too tight, OOS Sharpe goes -ve |

**Current best (iter 9):** composite -0.337, OOS Sharpe +0.43, stitched **-19.6%**
(vs baseline -47.4% = **+27.8pp improvement**), MaxDD 7%, 245 trades, TiP 65%,
PF 1.04 on prev-best comparison, payoff ratio 0.51, hit-rate 64%.

Params: `z_window=96, entry_k=2.0, exit_k=0.5, regime_quantile=0.15, vol_floor_q=0.5, regime_lb_days=30, trend_ema=50, trend_slope_window=5, atr_period=14`.

## Best stitched achieved this session

**-19.6%** at iter 9 (down from -47.4% baseline = +27.8pp). The winning
direction was **regime concentration**: keep only the entries that fall in
high-vol + tight-flat regimes where MR works (the regime decomp consistently
shows flat-trend buckets with Sharpe +3 to +9, while bull/bear trend buckets
bleed -3 to -14).

## What worked

1. **Vol floor raised** (0.2 → 0.5 ATR% quantile). Concentrates trades in
   v3/v4 buckets where MR edge is strongest. +15.5pp stitched.
2. **Regime tighter** (0.3 → 0.15 |slope| quantile). Reduces bull/bear bucket
   entries. +12.3pp stitched cumulatively.

These two single-knob tightening edits compound to flip the strategy from
deeply-bleeding to mildly-bleeding. Neither involves new signal families —
they are pure filter concentration.

## What was ruled out (this session)

- **Cross-sectional ranking (rank-based XS-MR).** Catastrophic: 5113 trades,
  every regime bucket negative, -100% stitched. The constant rank churn on
  15m bars is structurally lossy after fees. Verdict: **dead end**.
- **Return-based z** (logret excursion). Worse than price-z (-56% stitched).
  The 4-bar return is too noisy to support MR; the rolling-z normalization
  decouples from price drift but the underlying edge isn't there.
- **Time-based stop (8h max hold).** Healed regime decomp dramatically (5/12
  healthy buckets, all flat buckets glow) but **stitched got worse** (-51%
  vs -47%). The time-stop crystallizes losses in mid-revert. Lesson: any
  "force exit" that's not signal-based crystallizes paper losses.
- **ATR-based price stop (3·ATR).** Same failure mode as time-stop: cuts
  positions that would have reverted. Verdict: **same family of failure as
  z-stop from session 1** — stops are structurally incompatible with this
  MR mechanism.
- **Tighter entry_k (2.0 → 2.5).** Fewer/cleaner trades but no stitched
  improvement; not worth the lost TiP.
- **regime_quantile 0.10.** Too tight — OOS Sharpe goes -ve. Concave
  optimum around 0.15.

## Honest assessment

The strategy is **marginal but not structurally dead**. We've moved stitched
from -47% to -20% (worst case before stitched-floor scoring) — a 27pp
improvement. But:

- Profit factor is **still 0.85–0.91** across all KEEPs. After fees+funding
  the trade-shape is fundamentally lossy; the regime filters merely reduce
  the bleed rate.
- Per-trade expectancy in cents is **negative** ($-0.42 to $-0.74 per trade).
- The strategy "wins" only because regime gates suppress most of its bad
  trades. The underlying MR signal hasn't been shown to have edge net of
  costs on this universe.
- All KEEP iters carry the flag "OOS Sharpe positive but stitched negative —
  edge lives in OOS slices only, suspect WF calendar bias". The mean OOS
  Sharpe is propped up by W1 (2024-Q4 → 2025-Q1) which is consistently
  +1.5 to +4.0 while other windows are flat/negative. Single-window
  dependency = selection bias risk.

**Salvageable?** Only as a filtered MR overlay (regime-conditional sizing
multiplier on top of a different base alpha), not as a standalone strategy.

## Recommended next direction

The same regime gate that fixes stitched also kills TiP and makes the
strategy a sniper that's only effective in narrow windows. To make this
actually profitable, the structural ceiling (PF < 1.0) has to break, which
means either:

1. **Different entry signal that doesn't share family with the gate.**
   The current `z < -2 AND flat-4h AND vol > median` is three correlated
   filters on the same "deep mean-reversion at high vol" pattern. Try
   **Connors RSI(3)** with the SAME regime+vol filters — a different
   signal architecture using the same favorable regime might lift PF
   above 1.0 by detecting reversion turns earlier.
2. **Funding-rate filter** as the gate, not 4h slope. Funding directly
   measures market positioning skew; in flat-funding periods MR edge
   should be cleaner (no carry headwind on either side). Requires loading
   funding parquets that currently aren't in the harness path; check
   `data/bybit/funding/` first.
3. **Abandon symmetric L/S and go long-only with very tight gates.** Per
   session 1 priors, long-only iter 10 had best stitched at the time.
   Combined with the now-validated high-vol + tight-flat gates, this
   might be the cleanest path to PF > 1.

**Pick #1 first** (Connors RSI inside the validated gates) — it's the
smallest risk and tests whether the regime gate is doing the work or
the z-signal contributes anything.
