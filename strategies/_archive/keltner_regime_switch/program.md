# Keltner Regime-Switch research log

## Premise

Both pure breakout (`strategies/keltner`) and pure mean-reversion (rejected
in keltner iter 4) failed in isolation on this asset class:
- Breakout works only in trending regimes; in chop it whipsaws.
- MR works only in ranging regimes; in trends it's runover.

Hypothesis: **switch between them based on a regime classifier**. Same
Keltner channel, sign of response flipped by ADX bucket.

## Baseline (iter 1)

BTCUSDT 4h, single-symbol. Keltner EMA(20) ± 2.0·ATR(10).
Regime classifier: ADX(14).
- ADX > 25 → momentum (discrete +1/-1/0 by band breach)
- ADX < 20 → mean-reversion (continuous fade, clip to ±1)
- 20-25 → flat (hysteresis dead zone)

Long+short. No vol targeting. No higher-TF filter. The simplest possible
two-regime switch.

## Targets to beat

| Benchmark | What |
|---|---|
| Pure Keltner-breakout (CPCV median +0.45) | switch must hit ≥ 0.7 to claim alpha-from-architecture |
| Pure Keltner-breakout (WF composite -0.52) | composite must turn positive |
| BTC b&h on this period | Sharpe ~2.3 (tough but the right north star) |

If CPCV median lands at 0.4-0.5, the switch added complexity without edge.
If it lands at 0.7-1.0, the regime architecture genuinely helps.
If it lands above 1.5, we have a real candidate.

## What's been ruled out

- **Momentum-only on single-symbol BTC (iter 2, REVERT)** — regime-gated
  breakout fires too rarely (8 OOS trades, low-trades penalty). Architecture
  needs the MR half just to be eligible on single-symbol.
- **MR-only on single-symbol BTC (iter 3, REVERT)** — composite -3.93,
  worst yet. Continuous fade gets run over in W2/W3 trends despite ADX<20
  filter; W1 positive (+2.81) but isolated.
- **Asymmetric MR long-only (iter 4, REVERT)** — clipping MR shorts to 0
  hurt W4 (-6.37). MR shorts had value in some windows; the failure mode
  is inconsistent, not a clean "longs-good shorts-bad" pattern.
- **State-machine MR (iter 6, REVERT)** — classical "enter at band, exit at
  middle" with held position cut n_trades to 9, hit penalty. The trades it
  did take weren't materially better quality than continuous fade.
- **Adaptive ADX-tercile classifier solo (iter 7 KEEP marginal, iter 11
  REVERT in basket context)** — barely helped on single-symbol (-2.95 →
  -2.63 mainly via reduced cross-window variance). On multi-symbol it
  marginally beat fixed thresholds (-0.56 vs -0.66) — kept in champion.
- **MR-side useful at all in multi-symbol (iter 10, KEEP-with-MR-disabled)**
  — disabling MR on the basket *improved* composite -0.67 → -0.56 and
  OOS sharpe +0.43 → +1.21. **MR side adds noise, not edge.** The
  regime-switch architecture in practice collapses to "trend-only with
  ADX gate", i.e. close to pure breakout.
- **Faster htf gate (iter 12, REVERT, 50→20)** — 50 confirmed best, same
  as prior keltner research.
- **Wider multiplier 2.0→2.5 (iter 13, REVERT)** — n_trades to 30, penalty.
- **Faster ADX 14→7 (iter 14, REVERT)** — higher cross-window variance,
  composite -0.79.

## What's been tried (high-level)

| iter | hypothesis | verdict | composite | OOS sh | n_trades |
|---|---|---|---|---|---|
| 1 | baseline regime-switch (BTC, ADX 25/20, both halves) | BASELINE | -2.97 | -1.47 | 26 |
| 2 | ablate MR (momentum-only on BTC) | REVERT | -3.22 | -1.23 | 8 |
| 3 | ablate momentum (MR-only on BTC) | REVERT | -3.93 | -2.09 | 17 |
| 4 | asymmetric MR long-only | REVERT | -3.72 | -1.61 | 17 |
| 5 | + 1d EMA50 trend gate on momentum side | KEEP | -2.95 | -1.45 | 25 |
| 6 | state-machine MR (enter band, exit middle) | REVERT | -3.53 | -1.49 | 9 |
| 7 | + adaptive ADX-tercile classifier (rolling 90) | KEEP | -2.63 | -1.79 | 29 |
| 8 | ablate MR with adaptive + htf | REVERT | -3.22 | -1.39 | 7 |
| 9 | expand to 10-major basket | KEEP | -0.67 | +0.43 | 216 |
| 10 | ablate MR on basket | **KEEP** | **-0.56** | **+1.21** | 48 |
| 11 | ablate adaptive (fixed thresholds, basket) | REVERT | -0.66 | +1.03 | 52 |
| 12 | htf_ema 50→20 | REVERT | -0.62 | +1.20 | 48 |
| 13 | multiplier 2.0→2.5 | REVERT | -0.69 | +0.67 | 30 |
| 14 | adx_period 14→7 | REVERT | -0.79 | +1.20 | 54 |

## Champion (iter 10)

- 10-major basket × 4h × Keltner EMA(20) ± 2.0·ATR(10)
- ADX(14) regime classifier with rolling-tercile adaptive thresholds (90-bar window)
- Momentum-only branch (MR side disabled — empirically subtracted edge)
- 1d EMA50 trend gate on momentum side
- Composite -0.56 / OOS Sharpe +1.21 / max_dd 4.4% / DSR 0.66
- Per-window: -2.17 / +6.87 / -0.85 / +0.84

### Quality on champion
- `mean_sharpe_gap = -1.11` (OOS often *better* than train — not overfit)
- `worst_sharpe_gap = +3.14` (W2 had train Sharpe +0.5, OOS +6.87 — that's
  a regime-shift bonus, not a generalising edge; it's a large-tail event
  not a stable feature)
- `mean_pct_positive_months = 62.5%` (acceptable)
- `worst_longest_underwater_days = 38d` (manageable)
- `worst_pnl_concentration_top1_pct = 143%` (one trade > total PnL — fragile)
- `worst_pain_index = 2.6%` (clean)
- `mean_pct_time_in_position = 31%` (low duty cycle, cost-aware)
- `mean_alpha_sharpe = -1.10` — **strategy still loses to passive basket**

### CPCV (45 paths, 10 groups, k_test=2, 1D embargo)
- median Sharpe **+0.35**, IQR [-0.10, +0.75]
- 71% paths positive, 20% paths > 1.0
- worst max_dd 19.8%

## Architectural verdict — regime-switch did NOT add alpha

Compared to the prior pure-breakout champion (`strategies/keltner` iter 15):

| Metric | Pure breakout | Regime-switch (this) | Δ |
|---|---|---|---|
| WF composite | -0.52 | -0.56 | -0.04 |
| OOS Sharpe (mean) | +1.33 | +1.21 | -0.12 |
| CPCV median | +0.45 | +0.35 | -0.10 |
| CPCV pct positive | 73% | 71% | -2pp |
| WF alpha vs b&h | -0.97 | -1.10 | -0.13 |

**The regime architecture on this asset class is not earning its complexity.**
The MR side actively subtracts value (iter 10 confirmed: removing it
improved every metric). With the MR side off, what remains is pure
breakout with an added top-tercile-ADX filter — that filter does no
better than the ungated version from prior research, and arguably
slightly worse on CPCV. The full architecture's iter-1 baseline (-2.97)
existed *because* of the MR half adding noise; once we cut it, we
regressed back to vanilla breakout territory.

This is a **negative result on the architecture**: regime-switching does
not generate alpha on Keltner channels for crypto perp 4h. The data
does not support the hypothesis that "MR works in chop, momentum works
in trend, switch by ADX". The MR half loses money even when ADX < 20.

The research direction is **dead-end for this indicator family.** Suggest
the user pick a different family next:
- HMM regime classification (model-based, not threshold)
- Different MR signal (RSI / z-score) — Keltner band may simply be wrong
  signal for fading on perp BTC
- Pairs / cross-sectional MR (true statistical reversion, not directional fade)

## Open ideas

### Regime classifier alternatives
- Bollinger / Keltner band-width as regime signal (expanding = trend,
  contracting = range). Same indicator family, more direct.
- Hurst exponent (H > 0.5 = trending, H < 0.5 = mean-reverting)
- Realized-vol regime quantile bucket
- Combination: ADX + BBW agreement (only switch when both classifiers concur)

### Sub-strategy refinements
- MR with explicit exit at middle (state machine instead of continuous fade)
- MR with hard stop at lower − 0.5·ATR (cap downside in failed bottoms)
- Asymmetric thresholds: trend_long > trend_short (cap shorts in chronic uptrend)
- Time-stop on MR positions (exit if no progress in N bars)

### Sub-strategy enable flags
- `enable_momentum` and `enable_meanrev` exist as ablation flags. Disable
  one to attribute contribution cleanly:
  - momentum-only with regime gate ≈ pure breakout in trends, flat in chop
  - meanrev-only with regime gate ≈ pure fade in ranges, flat in trends

### Sizing / risk
- Conviction sizing on momentum side: |close - middle| / atr instead of ±1
- Vol-target on combined output (failed twice on pure breakout — but the
  combined position has different vol profile, may revisit)
- Drawdown-aware shrink in MR regime (MR drawdowns can be deep before reverting)

### Multi-symbol (after the switch is validated)
- Same architecture on the 10-major basket (use keltner v1 baseline as starting symbol set)
- Cross-sectional rank within basket (MR regime: long bottom decile by score, short top)

### Higher-TF filter
- 1d EMA gate (proven win in keltner v1) — apply only to momentum side?
  Or both? Probably only to momentum (MR by design fights the trend, so
  HTF gate would null it).

## Anti-patterns to avoid (METHODS §9 / AGENTS.md)

- Tuning trend/range thresholds to fit specific calendar periods
- Adding indicators on top of unprofitable base — if pure switch is REVERT,
  the architecture is wrong, not the parameter set
- Cherry-picking ADX threshold post-hoc to make W1 look better
- Using OOS performance to tune classifier
