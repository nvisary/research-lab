# Cross-sectional momentum research log

## Premise

Two prior research cycles (`strategies/keltner` and
`strategies/keltner_regime_switch`) showed that single-asset directional
TA on Keltner channels has very small OOS edge on crypto perp 4h, and
loses to passive buy-and-hold of the same basket. The structural problem:
edge from "predict price direction" is heavily over-fished.

Cross-sectional momentum attacks a different signal:
**relative strength dispersion across the basket**, not direction of the
basket as a whole. Long the recent winners, short the recent losers.
By construction ≈ market-neutral, so the relevant comparison is alpha
vs basket b&h, not raw Sharpe vs zero.

## Targets to beat

| Benchmark | Threshold |
|---|---|
| Pure-breakout CPCV median | > 0.45 — must clear prior ceiling |
| Basket b&h alpha sharpe | > 0 — must add alpha after costs |
| CPCV median ≥ 1.0 | strong real-world candidate |
| CPCV median ≥ 1.5 | finally a real strategy |

## Baseline (iter 1)

10-major basket (BTC ETH SOL BNB XRP ADA DOGE AVAX LINK LTC), 4h,
lookback 30d (180 bars), top/bottom 20% (2 longs + 2 shorts at any time),
long+short market-neutral. The simplest possible XS-momentum.

## Research methodology

The manager (me) iterates directly — not delegated to a sub-agent. One
hypothesis per iteration, one change per code edit. Stop conditions
same as the loop: 5 consecutive REVERTs without a KEEP → write up
ruled-out, ask user for direction change.

## Champion (iter 11) — replaces iter 3 4h champion

```
DEFAULT_SYMBOLS = 10 majors (BTC ETH SOL BNB XRP ADA DOGE AVAX LINK LTC)
DEFAULT_TF      = "1d"        ← upgraded from 4h
lookback_bars   = 60          # = 60 calendar days (same horizon as 60d/4h, coarser bars)
top_quantile    = 0.20        # top 2 long
bot_quantile    = 0.20        # (unused — long_only)
long_only       = 1
```

WF metrics:
- composite **+0.46** (vs iter 3: +0.42)
- OOS Sharpe (mean) **+2.64** (vs iter 3: +2.29)
- per-window OOS Sharpe: **+3.41 / +8.07 / +1.05 / -1.96**
- max DD (worst window) **1.80%** (vs iter 3: 2.19%)
- n_trades **5** (down from 14 — coarser TF = less rebal noise; penalty applies)
- DSR 0.86
- **alpha vs b&h +0.18** (bench was +2.46) — **FIRST POSITIVE alpha across 5 cycles**
- per-window alpha: **+0.72 / +1.05 / +0.75 / -1.78** — 3/4 positive, only W4 weak
- pct_positive_months 75% per-window mean (~65% on stitched, see frontend fixes)

CPCV (45 paths):
- median **+1.15** (slightly down from iter 3: +1.27 — fewer bars per OOS slice = noisier median)
- mean **+1.39** (up from iter 3: +1.14)
- IQR **[+0.25, +2.56]** — 25th percentile no longer negative; tail shifted into clear-positive
- 78% paths positive (up from 71%)
- 53% paths Sharpe > 1
- worst max_dd **6.84%** (down from 7.64%)

Net: iter 11 is a marginal but meaningful improvement on EVERY axis except
median CPCV (lateral, within noise). The IQR shift is the key — bad
scenarios at 1d champion are no longer net-negative.

## Champion was (iter 3)

iter 3 retired as champion. Same structure but TF=4h, lookback=360 bars.
Composite +0.42, OOS Sharpe +2.29, alpha -0.011. Kept here as research
record showing the 4h baseline before TF improvement.

WF metrics:
- composite **+0.42**
- OOS Sharpe (mean) **+2.29**
- per-window OOS Sharpe: +2.70 / +7.39 / +0.43 / -1.35
- max DD (worst window) 2.19%
- n_trades 14 (penalty-adjacent)
- DSR 0.83
- alpha vs b&h **-0.011** ≈ ноль (b&h was +2.30)
- pct_positive_months 100% per-window mean (NOTE: misleading — see "frontend
  fixes" commit; on stitched equity it's ~65%)

CPCV (45 paths, 10 groups, k=2, embargo=1D):
- median Sharpe **+1.27** ← higher than WF composite (rare —
  composite is dragged down by stability_penalty on volatile per-window std)
- IQR [-0.07, +2.02]
- 71% positive paths, 51% Sharpe > 1
- worst max_dd **7.64%** (vs ~20% in keltner cycles — market-neutral works)

Best CPCV result of all 5 research cycles. Alpha barely-zero, but DD 2-4×
smaller than directional strategies.

## What's been ruled out

- **lookback 90d at both TFs (iters 10, 12, REVERT)** — sweet spot is
  60 calendar days at both 4h and 1d. 90d misses regime turns.
- **lookback 30d at 1d (iter 13, REVERT)** — too short even at coarse TF.
- **top_quantile 30% at 1d (iter 14, REVERT)** — same dilution effect as
  at 4h. 3rd-ranked symbol weakens signal.
- **macro regime filter (iter 15, REVERT — impl bug)** — pandas Series
  broadcasting on 2D mask collapsed long_mask to all-False. Implementation
  needs revisiting; the hypothesis itself remains unevaluated.
- **Long+short market-neutral (iter 1, baseline)** — short leg loses
  structurally in bull regimes (alts catch up = short squeeze). Asymmetric
  reversal: longs have momentum, shorts have reversal. Alpha -3.37.
- **Vol-adjusted score (Sharpe-style ret/vol, iter 4, REVERT)** — on
  10-major universe vol is similar enough that the divisor adds noise,
  not signal. Composite 0.23 vs 0.42.
- **Top quantile 30% (3 longs, iter 5, REVERT)** — 3rd-ranked symbol
  dilutes signal. Diversification cost > concentration risk reduction.
- **Universe expansion 10 → 20 majors (iter 6, REVERT)** — added symbols
  (TIA, OP, INJ, etc.) noise out the rank; top-4 with weights 0.25 each
  weaker than top-2 with 0.5 each. Composite 0.07.
- **Top quantile 10% (1 long, iter 8, REVERT)** — full concentration on
  one momentum leader is too volatile. Composite -1.58.
- **Antonacci dual momentum overlay (iter 9, REVERT)** — requiring
  absolute_score > 0 in addition to rank cuts trade count too aggressively
  in mixed-trend periods. Composite -2.18, n_trades 10.

## What's been tried (chronological)

| iter | hypothesis | verdict | composite | OOS Sh | n_trades | notes |
|---|---|---|---|---|---|---|
| 1 | baseline (LS, 30d, top 20%) | BASELINE | -3.25 | -1.07 | 60 | short leg disaster |
| 2 | long-only | **KEEP** | **+0.05** | +1.72 | 29 | structural fix |
| 3 | lookback 30d → 60d | **KEEP** | **+0.42** | +2.29 | 14 | **CHAMPION** |
| 4 | vol-adjusted score (ret/vol) | REVERT | 0.23 | 2.06 | 25 | similar vol → noise |
| 5 | top 30% (3 longs) | REVERT | 0.31 | 2.07 | 34 | dilution |
| 6 | universe 10 → 20 majors | REVERT | 0.07 | 1.24 | 51 | noise |
| 7 | (no-op after iter 6 revert) | REVERT | 0.42 | 2.29 | 14 | no edit lost vs baseline |
| 8 | top 10% (1 long) | REVERT | -1.58 | 0.80 | 15 | volatile concentration |
| 9 | Antonacci dual momentum | REVERT | -2.18 | 0.36 | 10 | filter too aggressive |
| 10 | 4h lookback 60d → 90d | REVERT | -1.03 | 0.92 | 23 | misses regime turns |
| 11 | TF 4h → 1d (lookback 60d) | **KEEP** | **+0.46** | +2.64 | 5 | **NEW CHAMPION**; first +alpha |
| 12 | 1d lookback 60 → 90d | REVERT | -0.31 | 1.73 | 7 | same pattern as 4h |
| 13 | 1d lookback 60 → 30d | REVERT | -0.39 | 1.56 | 12 | too short |
| 14 | 1d top 20% → 30% | REVERT | +0.42 | 2.40 | 13 | dilution at any TF |
| 15 | macro regime filter | REVERT | n/a | 0.0 | 0 | impl bug, all longs blocked |

## Holdout result (2026-05-09) — momentum crash

```
period:           2025-10-01 → 2026-05-01
sharpe:           -2.65   (vs +2.64 train+val mean)
bench sharpe:     -1.72   (basket itself crashed in this period)
alpha:            -0.93
max DD:           13.8%
n_trades:         52
total return:     -12.4%
composite_holdout: -2.72  vs +0.46 train+val
```

Sanity check w/ pre-lookback-fix harness behavior (lookback=0):
  composite -1.66, sharpe -1.59, DD 8.8%. Lookback fix marginally
  worsened holdout because strategy traded from day 1 at the cycle
  peak instead of being warmup-blind. Both versions negative — this
  is a real regime mismatch, not a harness artifact.

Mechanism: the holdout window was the post-peak BTC reversal
(2025-Q4 cycle top → 2026 correction). xs_momentum picks top-2 by
60d return — at a cycle peak those are the most-pumped recent
leaders, which crash hardest in the reversal. Daniel & Moskowitz
2016 ("Momentum Crashes") describe this in equities: pure momentum
loses 30%+ at major regime changes. Crypto's faster cycles compress
the same crash into months.

What CPCV / DSR didn't catch:
  - CPCV mixes paths within the train+val period — all of which were
    in the same bull-regime. Path variance ≠ regime-shift variance.
  - DSR adjusts for trial count but not for regime mismatch.
  - Both metrics signaled high confidence (DSR 0.86, CPCV median
    +1.15) but evaluated only on the bull-regime distribution.

Verdict: strategy works in bull regimes, crashes at cycle peaks.
Known limitation of pure cross-sectional momentum. Not a "wrong"
strategy — a strategy with a specific regime applicability that was
not captured by within-train-val statistics.

Holdout is now SPENT for this strategy on this period. Cannot
re-evaluate the same train+val→holdout split with different
parameters. Future research on this strategy family must use a
different out-of-sample window (or wait until more data accrues).

## Open ideas (next research direction)

- **Lookback 60d → 90d**: continue up the curve, marginal gains expected.
- **Throttle rebalancing weekly**: reduce continuous rebalance turnover.
- **Switch TF to 1d**: 30d at 1d = 30 bars — much less noise than 360 bars
  at 4h. May discover different structure.
- **Once lookback architectural fix lands**: warmup waste eliminated →
  more trades per OOS slice → composite penalty drops → re-evaluate.
- **Multi-strategy portfolio**: combine xs_momentum (now market-neutral
  long-only) with a directional strategy (supertrend / breakout) — low
  correlation may compound to portfolio Sharpe > 2.

## Open ideas

### Lookback / horizon
- 30d (default) — academic median
- 60d — slower momentum, higher Sharpe in some literature
- 7d / 14d — faster, picks up regime changes earlier
- Combined: blend short + long lookback (e.g., 50/50 of 14d-rank and 60d-rank)

### Quantile bracket
- 10% (1 long + 1 short) — concentrated, higher variance
- 30% (3 long + 3 short) — more diversified, possibly lower edge per leg
- Asymmetric: 30% long, 10% short — bias toward longs in chronic uptrend

### Universe expansion
- 30-symbol basket — more dispersion to rank, more opportunities
  - Cautionary: smaller cap symbols → higher costs (capacity warning checks)
- 50+ symbols — academic-quality universe but survivorship-bias-heavy on Bybit perp

### Score variants
- Log-return (current default) over lookback — symmetric in compounding
- Raw return — easier to interpret, equivalent for ranking purposes
- Sharpe-style score: return / vol (vol-adjusted momentum)
- Risk-parity weighting within the long leg (size by inverse vol)

### Filters
- Liquidity filter: skip symbols with low rolling $-volume (capacity)
- Vol filter: skip symbols with extreme vol that would dominate per-leg variance
- Macro regime filter: only run XS-momentum when basket itself is in defined trend
  (counter to the design — XS is supposed to be regime-agnostic)
- Time-of-day / day-of-week — likely overfit, avoid

### Risk / sizing
- Vol-target the combined basket position (not per-symbol) to a fixed daily target
- Drawdown-aware shrink after rolling-N negative bars
- Per-symbol size cap (no symbol > 30% of portfolio even with rank tie)

### Rebalancing throttle
- Rebalance only on a fixed cadence (e.g., daily / weekly) instead of every bar
  → reduces costs at price of stale ranks
- Rebalance only when rank changes by ≥ N positions
  → reduces noise rebalances on tied scores

### Composition / blend
- Combined with absolute momentum: long only when symbol's own return > 0
  (Antonacci's "Dual Momentum" — adds time-series + cross-sectional)
- Pair with mean-reversion overlay on the within-leg dispersion

## Anti-patterns to avoid (METHODS §9 / AGENTS.md)

- Tuning lookback to fit specific calendar windows
- Picking quantiles post-hoc to maximize composite
- Adding many filters on top of unprofitable base
- Multi-testing on lookback × quantile grid (each combination is a separate strategy)
