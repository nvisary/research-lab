# mom_tsmom — time-series momentum (per asset)

## Baseline

Per symbol on 1d bars: sign of 14-day trailing return is the
position direction. Long if return > 0, short if return < 0,
flat at exact zero. Each asset evaluated independently across the
10-major basket.

```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "1d"
lookback        = 14
long_only       = 0
```

## Hypothesis

Past N-day return predicts the sign of the next N-day return. The
classic TSM result (Moskowitz, Ooi, Pedersen 2012) extended to
crypto majors. Long-bias of crypto historical drift means TSM
should pick up steady up-trends in BTC/ETH and ride flips in alts.

## Why this slot in the quadrant

Time-series momentum. Per-asset, absolute direction. Orthogonal
to mom_xsection (which is rank-based) — TSM fires when the WHOLE
basket trends one direction, while CSM fires on dispersion.

In a uniform bull market, TSM is fully long all 10 symbols; CSM is
balanced 30/30 long-short. In a chopping market with idiosyncratic
movers (e.g. one alt rallies on its own), TSM may be flat while
CSM longs the rallying alt and shorts the laggards.

## What's been tried (iters 1–20)

User asked to fix shorts (poor on 2025 drops) and lift 2025 PnL.

| iter | hypothesis | verdict | composite |
|---|---|---|---|
|  1 | baseline (lookback=14, sign-of-ret, basket of 10) | BASELINE | −0.25 |
|  2 | long_only=1 — probe shorts hurt? | REVERT | −2.50 (shorts CRITICAL — W3 falls to −3.51) |
|  3 | lookback 14→30 | REVERT | −0.44 (W3 rescued but W0/W1 hurt) |
|  4 | short HTF gate (90d) | REVERT | −1.40 |
|  5 | entry_threshold=5pct | REVERT | −0.57 |
| **6** | **asymmetric: long=14d, short=30d** | **KEEP** | **+0.20** (Δ+0.45, W3 −0.77 → +1.22!) |
|  7 | short_lookback 30→45 | REVERT | 0 trades penalty |
|  8 | long_lookback 14→21 | REVERT | −0.22 |
|  9 | vol-target 2pct | REVERT | +0.20 (just below +0.01 threshold; DD ↓ but sharpe ↓) |
| 10 | skip-7d (12-1 month classic) | REVERT | −2.23 |
| 11 | short_lookback 30→21 | REVERT | −0.28 |
| 12 | conviction sizing scale=5 | REVERT | −0.62 |
| 13 | shrink basket to 3 (BTC/ETH/SOL) | REVERT | only 8 trades |
| 14 | regime gate 90d both legs | REVERT | −0.90 |
| 15 | long_lookback 14→7 | REVERT | +0.07, DD 24pct |
| 16 | dual-agreement (both signals same sign) | REVERT | −0.30 |
| 17 | short_threshold=3pct | REVERT | −0.34 |
| **18** | **+ ema_smooth=3 days on close** | **KEEP** | **+0.46** (Δ+0.26, OOS sharpe +1.35) |
| 19 | ema_smooth 3→5 | REVERT | only 9 trades |
| 20 | ema_smooth 3→2 | REVERT | +0.08 |

## Champion (iter 18) parameters

```
DEFAULT_TF      = "1d"
lookback        = 14   (long signal: sign of 14d return)
short_lookback  = 30   (short signal: sign of 30d return — slow confirm)
ema_smooth      = 3    (3-day EMA on close before momentum calc)
long_only       = 0
DEFAULT_SYMBOLS = 10 majors
```

Per-window OOS sharpe:
- W0 (24-H1): +1.15
- W1 (24-H2): +3.16
- W2 (25-H1): −0.26
- W3 (25-H2): +1.34

OOS sharpe +1.35, max DD 12.4pct, 14 trades, DSR 0.64.

User asks revisited:
1. **Shorts work poorly** — FIXED. W3 (25-H2 cycle peak with strong drops) went from
   baseline −0.77 to champion +1.34. Asymmetric short_lookback=30 stops 14d-flip whipsaws
   on counter-trend bounces; ema_smooth=3 cuts daily noise that triggered false reversals.
2. **2025 was in the red** — PARTIALLY FIXED. W3 fully rescued (+1.34). W2 (25-H1) still
   slightly negative (−0.26 vs −0.58 baseline) — improved but the regime (Q1-Q2 2025
   alt rotation with sharp BTC pullbacks) remains hostile to TSM. Combined 2025 now
   mixed instead of uniformly red.

## What's been ruled out

- **No-shorts (long_only)** — shorts contribute meaningfully; W3 needs them.
- **Slow long signals (21+, 30d)** — too slow for bull rallies (W0/W1 hurt).
- **Skip-week (classic 12-1)** — crypto doesn't show the short-term reversal that
  equity momentum factor relies on. −2.23 catastrophic.
- **Magnitude / hard threshold filters** — destroy edge by cutting marginal-but-correct
  signals. −0.57 (5pct) and −0.34 (3pct short-only).
- **Regime gates (90d both legs)** — too restrictive, cuts legitimate trades.
- **Dual-agreement** — drops choppy bars but loses trade count.
- **Basket shrinkage to top-3** — sample size collapses (8 trades on 24mo).
- **Conviction sizing** — small signals were valuable, not noise.

## Round 2 (post-fresh-baseline, with new harness capabilities)

After harness gained RAW_SIZING, MAX_POSITION, and rich diagnostics
flags, we reset history and re-baselined on the previous champion
(asymmetric long=14d short=30d, ema_smooth=3, basket=10). Then 10
iters with the new tools.

| iter | hypothesis | verdict | composite |
|---|---|---|---|
|  1 | baseline = previous champion | KEEP | **+0.46** |
|  2 | RAW_SIZING parity check (per-asset 1/n) | REVERT | +0.46 (identical — RAW math correct) |
|  3 | RAW + vol-target 1pct/day per leg | REVERT | +0.40 (DD ↓ 11.3pct→6.6pct, sharpe ↓) |
|  4 | vol-target 1.5pct (softer) | REVERT | +0.35 |
|  5 | RAW + conviction `tanh(ret*5)` | REVERT | −0.24 (tanh shrinks avg size) |
|  6 | shrink basket to top-5 | REVERT | −0.86 (only 8 OOS trades) |
|  7 | RAW + slot=20pct + MAX_POS=0.3 | REVERT | +0.33 (DD↑ 17.9pct, fat-tail 50pct) |
|  8 | BTC-led regime gate (30d) | REVERT | −0.84 (DSR flagged ↓0.23 by diagnostics) |
|  9 | short_lookback fine-tune 30→25 | REVERT | −0.40 |
| 10 | ema_smooth fine-tune 3→4 | REVERT | +0.10 |

**Champion remains iter 1** (composite +0.46, OOS Sharpe +1.35, DSR 0.86).

### What's been ruled out (Round 2)

- **Vol-targeted sizing** (1.0–1.5pct daily target): reduces DD but
  proportionally cuts edge. Equal-weight basket already self-diversifies
  via uncorrelated alts; risk-equalising doesn't add alpha here.
- **Conviction sizing** (tanh of momentum): bounded shape shrinks
  typical |ret|*5 below 1, so most positions become smaller, hurting
  capture of the strong trends TSM relies on.
- **Basket shrinkage** (5 majors): trade count collapses, sample-size
  punishes composite via penalty + fat-tail dependence rises.
- **Per-signal slot oversizing** (20pct/signal with MAX_POSITION=0.3):
  blew DD up to 17.9pct, fat-tail dependency to 50pct — concentration
  risk overwhelmed any benefit.
- **BTC-led regime gate** (30d sign): cuts too many entries; hurt
  composite by 1.3 points and triggered DSR-decay diagnostic flag.
- **Fine-tune short_lookback** (25 between 21-revert and 30-champion):
  monotone — 30 is the sweet spot.
- **Fine-tune ema_smooth** (4 between 3 and 5-revert): 3 is sweet spot.

### Lesson

The new diagnostic flags (especially "DSR down from peak" and
"sharpe_gap W_X > 1.0") fired correctly on bad iters and would have
been visible after each iter — confirms the diagnostics-as-self-check
loop the agent was given works as intended.

The champion appears to be near a genuine local optimum on this
universe + period. Further iteration is likely overfit harvest, not
edge improvement. Run CPCV before treating it as worth deploying.

## Open questions

- Lookback sweep — 7 / 14 / 30 / 60 / 90 days
- Vol-targeted sizing: position = sign(ret) × clip(target_vol / asset_vol, 0, 2)
- Skip latest 1 day (anti-microstructure)
- Asymmetric thresholds (e.g. require |ret| > 5% to enter) — reduces churn
- Funding-aware: shorts cost funding in long-bias regimes
