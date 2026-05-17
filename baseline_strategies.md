# baseline_strategies — distilled ideas from prior research

Compact knowledge log distilled from 12 strategies across the framework.
Iteration histories, best.json, equity/trades parquets, and per-strategy
`runs/` directories were discarded as **stale** (framework evolved:
composite formula now includes time-in-position penalty; RAW_SIZING /
MAX_POSITION added; rich diagnostics flags; data periods rebalanced;
funding accounting; etc.). **Only the ideas, what worked, what didn't,
and the open angles survive.**

Each entry below preserves:
- **Slot** — where the strategy sits in the trend/MR × per-asset/XS quadrant.
- **Baseline thesis** — the mechanism encoded by the simplest version.
- **Best config found** (parameters only — numbers will drift on a fresh run).
- **What worked** — non-obvious primitives that earned their place.
- **What was ruled out** — refuted hypotheses (avoid re-trying without a new reason).
- **Open angles** — untried directions worth exploring.
- **Key lesson** — the structural insight that carries to other strategies.

---

## Strategy quadrant map

|                | **Per-asset (TS)**       | **Cross-sectional (XS)**           |
|----------------|--------------------------|-------------------------------------|
| **Trend**      | mom_tsmom, trend_supertrend, keltner, keltner_regime_switch | xs_momentum, mom_xsection |
| **Mean-rev**   | mr_zscore, mr_zscore_meta, bb_rsi_meanrev | mr_xsection                |
| **Stat-arb**   | —                        | pairs, pairs_trading                |

---

# Trend-following — per asset (TS)

## 1. mom_tsmom — time-series momentum (sign of N-day return)

**Thesis.** Past N-day return predicts the sign of the next N-day return (Moskowitz/Ooi/Pedersen 2012 applied to crypto majors). Per asset, sign-of-trailing-return → position.

**Best config (after 2 sessions, ~30 iters):**
```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "1d"
lookback        = 14         # long signal: sign of 14d return
short_lookback  = 30         # short signal: sign of 30d return (slow confirm)
ema_smooth      = 3          # 3d EMA on close before momentum calc
long_only       = 0
```

**What worked:**
- **Asymmetric lookback** (fast long / slow short). Crypto long-bias drift means shorts need deeper confirmation to avoid bounce-fade whipsaws. Reportedly rescued W3 (cycle-peak) from sharply negative to positive.
- **EMA smoothing of close** (span=3) before pct_change — cuts daily noise that triggered false sign-flips.

**Ruled out:**
- `long_only=1` — shorts contribute meaningfully in cycle-reversal regions (W3-style).
- Slow long-lookback (21d+) — too slow for bull rallies (W0/W1 hurt).
- Classic 12-1 skip-week rule — crypto doesn't show the short-term reversal that equity-momentum factor relies on. Catastrophic on this universe.
- Magnitude / hard-threshold filters — destroy edge by cutting marginal-but-correct signals.
- Basket shrinkage (3 majors) — sample-size collapses (~8 trades on 24mo).
- Conviction sizing (`tanh(ret * 5)`) — bounded shape shrinks typical positions below 1, hurting capture.
- Vol-targeted sizing (1.0–1.5% daily) — proportionally cuts edge; equal-weight basket already self-diversifies via uncorrelated alts.
- BTC-led 30d regime gate — cuts too many entries; triggered DSR-decay flag.
- Per-signal slot oversizing (20% / signal with MAX_POSITION=0.3) — blew DD up, fat-tail dependency rose to 50%.
- Fine-tune `short_lookback` 25 (between 21-revert and 30-champion) — monotone, 30 is sweet spot.
- Fine-tune `ema_smooth` 4 (between 3 and 5-revert) — 3 is the optimum.

**Open angles:**
- Funding-aware: shorts cost funding in long-bias regimes — skip shorts when funding is highly negative.
- Skip latest 1d (anti-microstructure).
- Vol-targeted sizing combined with another change (didn't work solo).

**Key lesson.** Asymmetric long/short windows are a clean primitive against long-bias drift. Don't smooth too hard (EMA span 3 worked; 4-5 broke).

---

## 2. trend_supertrend — SuperTrend ATR bands

**Thesis.** ATR-adaptive bands give cleaner trend definition than fixed-N TSM: bands widen in vol, tighten in quiet — fewer whipsaws in chop, faster reversal capture in quiet.

**Best config (after ~46 iters across 5 batches):**
```
DEFAULT_SYMBOLS    = 10 majors
DEFAULT_TF         = "1d"
atr_period         = 11
multiplier_long    = 2.0
multiplier_short   = 0.75       # tighter shorts — fast flip-out
```
Filters stacked:
- Weekly EMA10-slope binary gate (long only when weekly slope > 0; short only when < 0).
- Monthly EMA3-slope agreement gate (trend-existence filter).
- 1d ADX(14) > 20 trend-strength filter.
- Asymmetric vol-scaling on shorts only: `short_pos *= clip(0.02/atr_pct, 0.25, 1.0)`.

**What worked (in order of impact):**
- **Asymmetric multiplier** (long=2.0, short ∈ {0.65, 0.75, 0.85} plateau). First positive composite. Logic: shorts whipsaw faster under long-bias drift → tighter band = faster flip-out. Plateau confirmed by bracketing on both sides.
- **ADX(14) > 20** trend-strength filter — single biggest additive primitive (1 KEEP out of 10 candidates).
- **Weekly EMA10-slope binary gate** — align entries with weekly trend.
- **Monthly EMA-slope agreement gate** — additional trend-existence check.
- **Vol-scaling on shorts only** — cuts DD ~50%, brings worst_month from -11% to -3% without sacrificing long-side trend rides.
- **`atr_period = 11`** marginally beats 10; sweet-spot plateau {10, 11}.

**Ruled out:**
- `long_only=1` — kills W4 (-6.44 sh, 2 trades). Shorts are needed for cycle-reversal regions.
- Vol-normalized sizing applied symmetrically — clean DD-primitive (-67% DD) but cuts Sharpe by 25%; not a standalone Sharpe-additive.
- Shorter ATR (atr_period=7, 5) — doesn't fix trade scarcity; weekly slope gate is binding.
- ATR multiplier wider (2.5, 3.0) — fewer trades, worse Sharpe.
- Magnitude weekly-slope gate — EMA10-normalized slope is too small for sensible thresholds.
- Per-symbol regime conditioning (ADX top-quartile, ADX-vs-own-median, ADX-vs-own-mean, vol-band) — 4 consecutive REVERTs. Basket-uniform ADX>20 has a genuine asymmetric effect that disappears when normalized per-symbol.
- ADX-rising requirement — kills mid-build trends.
- +DI/-DI direction agreement on top of ADX — redundant with SuperTrend's own direction.
- Cost-aware flip-buffer — band itself already filters small moves; buffer just delays good entries.
- Slower weekly EMA20 gate — misses regime turns.
- ADX threshold tuning (15, 25) — 20 is the sweet spot.
- Universal drawdown-control reset (after N consecutive losing trades) — cooldowns reactivate exactly when SuperTrend re-flips → cuts recoveries, not losers.
- Basket consensus (≥6/10 weekly-EMA agree) — kills divergent good rides.
- Per-symbol bull-regime short-suppressor — improves v3-bull bucket but cost on aggregate Sharpe is larger.

**Open angles:**
- v3-bull lossy regime still uncured — try mechanism-tied attack (short-side ATR not 30d return).
- Asymmetric ATR period (long=11, faster for short).
- Drawdown-control reset using strategy equity, not per-trade.

**Key lesson.** Asymmetric L/S parameters are a structurally honest response to crypto long-bias. **Watch for Sharpe inflation via low time-in-position** — the framework now penalizes `pct_time_in_position < 20%` directly because of this strategy's gaming pattern (kept tightening filters until activity collapsed).

---

## 3. keltner — Keltner channels breakout (single → basket)

**Thesis.** Breakout outside EMA ± N·ATR is the actionable signal; inside the band, sit out.

**Best config (after ~15 iters):**
```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "4h"
ema_period      = 20
atr_period      = 10
multiplier      = 2.0
long_only       = 0
htf_ema_period  = 50         # 1d EMA50 trend gate via resample_higher
```

**What worked:**
- **Multi-symbol basket (10 majors)** — biggest single jump (composite -1.76 → -1.10).
- **1d EMA50 HTF trend gate** — composite jump (-0.89 → -0.52).
- **Default multiplier 2.0 / ATR(10)** — wider (2.5) cuts trade count below penalty; tighter adds whipsaws.

**Ruled out:**
- `long_only=1` on basket — shorts in W3 (cycle-peak) were valuable.
- Mean-reversion variant (flip sign) — blew up W3 and W4 (band is not reliable fade signal on this universe).
- Vol-targeted sizing (single and multi-symbol) — cut upside more than downside; net negative.
- Wider multiplier (2.5) — n_trades fell below penalty, no quality gain.
- ADX > 20 gate AFTER 1d EMA gate — redundant.
- Same-TF SMA200 trend filter AFTER 1d EMA gate — redundant.

**CPCV reality check.** WF composite -0.52 looked like a foothold; CPCV median +0.45 with IQR [-0.07, +1.36], 73% paths positive, worst path DD 20%. **A real but ~3× weaker edge than WF suggests.** Tail DDs deeper than WF picture.

**Open angles:**
- Smooth-stop exit (close re-entering middle channel).
- Replace EMA centre with HMA / Kaufman AMA (less lag without overshoot).
- ATR-period adaptive to realized-vol regime.
- Funding-rate sign as long-bias filter.

**Key lesson.** Multi-symbol basket and HTF trend gate are independently load-bearing. The CPCV reality check is a critical reality dose: a small WF positive composite can be a much smaller true edge with deep tail DDs.

---

## 4. keltner_regime_switch — ADX-gated trend/MR switch

**Thesis.** Same Keltner channel, sign flipped by ADX regime: ADX>25 → momentum; ADX<20 → fade; 20-25 dead zone.

**Architectural verdict: negative result.** Regime-switching architecture on Keltner did NOT add alpha. The MR side actively subtracts value (composite -0.67 → -0.56 by disabling MR). The full architecture collapsed to "trend-only with extra ADX filter" — performing slightly worse than the vanilla breakout from `strategies/keltner`.

**Numbers:** WF composite -0.56, CPCV median +0.35, alpha vs b&h -1.10. All worse than pure breakout.

**Ruled out:**
- Momentum-only or MR-only on single-symbol BTC.
- Asymmetric MR long-only — clipping MR shorts to 0 hurt W4.
- State-machine MR with discrete entry/exit — cut n_trades to 9, hit penalty.
- Adaptive ADX tercile classifier — barely helped solo; on basket it marginally beat fixed thresholds but the architecture itself underperforms.
- Faster HTF gate, wider multiplier, faster ADX period — all worse.

**Key lesson.** **Negative architectural results matter** — they save future iteration cost. ADX-tercile regime classification doesn't add alpha to Keltner-band signals on crypto perp 4h. Don't retry this with the same indicator family. Try HMM, Hurst, or BBW-based classifiers if revisiting regime switching.

---

# Trend-following — cross-sectional (XS)

## 5. xs_momentum — top-N momentum long-only

**Thesis.** Long the recent best performers in the basket. Long-only (shorts have momentum-crash risk + funding drag).

**Best config:**
```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "1d"
lookback_bars   = 60     # 60 calendar days
top_quantile    = 0.20   # top 2 long
long_only       = 1
```

**Holdout result (this strategy was retired).** Train+val Sharpe +2.64 / composite +0.46. Holdout (2025-10 → 2026-05, post-cycle-peak) crashed to **-2.65 Sharpe, -12.4% return**. Classic momentum-crash (Daniel & Moskowitz 2016) — picks top-2 by 60d return, which are the most-pumped recent leaders that crash hardest on reversal. CPCV didn't catch it: all CPCV paths were drawn from bull-regime train+val data; path variance ≠ regime-shift variance.

**Ruled out:**
- Long+short market-neutral — short leg disaster in bull regimes (alt squeeze).
- Vol-adjusted score (Sharpe-style ret/vol) — on 10 majors vol similar enough that divisor noises out signal.
- Top quantile 30% (3 longs) — dilution.
- Top quantile 10% (1 long) — volatile concentration.
- Universe expansion 10 → 20 majors — adds noise to rank, weakens signal.
- Antonacci dual momentum (require absolute > 0 AND rank top) — too aggressive, n_trades 10.
- Lookback 30d at 1d — too short; 90d — misses regime turns.

**Key lesson.** **CPCV and DSR don't catch regime mismatch** — they evaluate within the train+val distribution. Pure cross-sectional momentum is a known momentum-crash vehicle; should be paired with regime filter or combined with mean-reverting overlay before declaring a holdout-worthy candidate. The strategy's edge IS real (in bull regimes); the failure is in claiming it's regime-agnostic when it isn't.

---

## 6. mom_xsection — long/short XS momentum on extended universe

**Thesis.** Rank basket by trailing return, long top quantile / short bottom. Cross-sectional dispersion = alpha that TSM averages away.

**Best config (after 3 sessions, 40 iters):**
```
DEFAULT_SYMBOLS = 45 коинов (10 majors + 15 large-caps + 20 mid-caps)
DEFAULT_TF      = "1d"
lookback        = 30     # ≤ OOS_window / 2 rule
long_quantile   = 0.3
short_quantile  = 0.3
hold_days       = 1      # continuous rebalance
vol_target      = 1      # enabled
vol_window      = 30
```

**What worked (in order):**
- **Universe expansion 10 → 25 → 45** — biggest structural fix. Broke dependency on single-coin pumps. Bear-regime buckets fixed (v1-bear Sharpe -7.5 → +12.4).
- **`hold_days = 1` continuous rebalance** — strategy in position ~95% of bars; solves the "strategy mostly flat" problem.
- **Vol-target per leg (1/σ)** — on continuous + 45 coins equalizes risk, lifts Sharpe ~0.7, makes PnL distribution flatter (top-1 trade share 63% → 8.8%).
- **`lookback = 30`** — short enough to not warmup out the OOS slice. Hard rule discovered: `lookback ≤ OOS_window / 2`.
- **`q = 0.3`** sweet spot. 0.2 / 0.4 / 0.5 worse.

**Ruled out:**
- Short lookbacks ≤ 60 on 10 majors — bear-buckets Sharpe -7..-3.
- Long lookbacks ≥ 180 on 10 majors — too few OOS trades; warmup eats OOS slice.
- 12-1 skip-week — never works on crypto, all universes.
- Vol-normalized ranking (z-score instead of raw return) — kills edge on majors; high-vol IS high-momentum on this asset class.
- BTC trend gate (SMA200 / SMA100 full or short-leg-only) — too jerky; bear-buckets aren't BTC-trend issue.
- Dispersion gate (252d rolling) — warmup kills W0; 60d also didn't recover edge.
- Position cap 15% — doesn't bind when vol-target is on (already balanced).
- Lookback fine-tune (20, 45, 90) — all worse than 30 on 45-coin universe.

**Open angles:**
- Universe 60-80 coins.
- Asymmetric quantiles (long_q=0.3, short_q=0.2).
- Funding-rate filter.
- 4h timeframe with proportional lookback (~120-180 4h bars = 5-7.5d).

**Key lesson.** **`lookback ≤ OOS_window / 2`** is a hard rule discovered the hard way: a strategy that warms up through half its OOS slice has half its OOS as NaN positions, producing artificially high Sharpe on small effective sample. **Universe size matters far more than parameter tuning** on cross-sectional strategies — expansion from 10 to 45 was the single biggest improvement across the whole research program.

---

# Mean-reversion — per asset (TS)

## 7. mr_zscore — per-asset z-score MR (1h)

**Thesis.** When asset price is at statistical extreme (|z| > 2σ over rolling N-bar window), next move more likely to be mean-reverting than continuing.

**Baseline only (no completed iteration program preserved). The thesis stands; the parameter space is uninvestigated.**

```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "1h"
zwindow         = 168     # 1 week of 1h bars
z_thresh        = 2.0
z_exit          = 0.0
long_only       = 0
```

**Open angles (all untried):**
- `z_thresh` sweep — 1.5 / 2.0 / 2.5.
- `zwindow` sweep — 1d (24) / 3d (72) / 1w (168) / 2w (336).
- Asymmetric z_exit (tighter for shorts due to funding drag).
- HTF trend gate (only long when 1d close > 1d EMA).
- Cost-aware skip when |z| marginally above threshold.

---

## 8. mr_zscore_meta — z-score MR + LogReg meta-labeler

**Thesis.** Same primary signal as mr_zscore, but each candidate trade gets a meta-labeler (LogReg trained on triple-barrier outcomes) that decides whether to take it. López de Prado-style secondary classifier.

**Status: demo / baseline only.** Worth keeping as the **scaffolding** for meta-labeling, not as a strategy to iterate on directly. The pattern (primary signal → meta-labeler with regime/vol/momentum features → triple-barrier labels) is reusable across any primary signal.

**Open angle.** Use the same meta-labeler scaffold on better primary signals (e.g. BB+ADX, SuperTrend, XS momentum). Meta-labeling has more to offer when applied to a signal with positive raw edge.

---

## 9. bb_rsi_meanrev — Bollinger Bands + RSI mean reversion (1h)

**Thesis.** Band touch + RSI exhaustion in low-trend regime (ADX<20) → MR entry. Exit at mid-band or ATR stop.

**Best config (after ~20 iters):**
```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "1h"
bb_period       = 20
bb_std          = 2.5         # deep extreme touch (was 2.0)
rsi_period      = 14
rsi_long        = 100         # RSI gate DISABLED (it cut good signals)
rsi_short       = 0
adx_period      = 14
adx_max         = 25
atr_period      = 14
atr_stop_mult   = 3.5         # wide stop survives transient pullbacks
long_only       = 1           # shorts in MR on perps fight funding
htf_ema_period  = 100         # 1d EMA100 trend gate
```

**What worked (in order):**
- **Drop the RSI gate** — surprising: filtered out the very signals the MR thesis depends on. The "BB+RSI" name ended up dropping RSI entirely.
- **Multi-symbol basket (5 → 10 majors)** — biggest single jump.
- **`long_only = 1`** — shorts in BB MR on perps lose to funding drag in bullish-drift regimes.
- **1d EMA100 HTF trend gate** — don't fade against the daily trend (EMA50 worked; EMA75 and EMA200 did not).
- **`bb_std = 2.5`** — deeper extreme touch beats default 2.0.
- **Wide ATR stop (3.5)** — survives transient pullbacks in cycle-peak windows; tight stop (2.5) hurt W3.

**Ruled out:**
- RSI confluence filter (the original thesis's headline component).
- BB-width gate AFTER ADX gate — over-filtering.
- Opposite-band take (full mean-reversion target) — most reversions don't reach the far side.
- Shorts — funding drag is structural, not noise.
- ADX < 20 (tighter than 25) — over-filters once HTF gate is in.
- Trailing stop (max_close − 2.5·ATR) — DD improved, but W3 broke.
- `bb_period = 30` (slower mean) — n_trades 31, W3 broke.

**Open angles:**
- Asymmetric RSI thresholds (25/75) — fewer but cleaner signals (untried after RSI was dropped).
- Entry on band breach + reversal candle (close back inside band) — fewer knife-catches.
- Cost-aware skip when `(band − mid)/close < N · taker_fee`.
- Conviction sizing: `|close − mid| / (bb_std · σ)`.

**Key lesson.** **The names of strategies lie.** "BB+RSI" became "BB+ADX+HTF gate, long-only, no RSI". Be ready to throw out the namesake component if it loses on its own. The surviving primitives were: multi-symbol diversification (the biggest single jump in every strategy where it was tried), long-only on perps (when funding drag is structural), and HTF trend gate. **These three carry forward into almost every other strategy.**

---

# Mean-reversion — cross-sectional (XS)

## 10. mr_xsection — XS mean reversion (basket-relative)

**Thesis.** Within a correlated basket, the recent worst performer outperforms the recent best over the next N bars. Rank-based; relative within basket.

**Baseline only (no completed iteration program preserved).**

```
DEFAULT_SYMBOLS = 10 majors
DEFAULT_TF      = "4h"
lookback        = 84    # 14d at 4h
long_quantile   = 0.3   # bottom 30% (losers go long)
short_quantile  = 0.3   # top 30% (winners go short)
long_only       = 0
```

**Open angles (all untried):**
- Lookback sweep — 7d / 14d / 30d.
- Quantile sweep — 0.2 / 0.3 / 0.4.
- Hold period — continuous vs N-bar hold (reduce turnover).
- Skip latest 1d (anti-microstructure).
- Vol-normalized z-rank instead of raw-return rank.

**Implicit lesson from `mom_xsection`.** Universe expansion (10 → 45) was the biggest single improvement on long XS momentum. The mirror image on XS-MR is likely to be **even more important** — MR needs dispersion to operate, and 10 majors is a narrow rank space.

---

# Stat-arb / pairs

## 11. pairs — cointegration-ranked pairs (ported from pairs_bot)

**Thesis.** Cointegrated log-price spreads in the broad Bybit perp universe are mean-reverting. Trade top-N pairs by score (ADF + half-life), Z>2 entry / Z<0.5 exit / Z>4 hard-stop, refit on 30d rolling window every 7d.

**Baseline (not fully iterated yet).** Universe: 173 currently-listed Bybit USDT-perps (full breadth). TF: 1h.

**Critical caveat — survivorship bias.** The universe is **currently-listed only**. Pairs that broke and got delisted are absent by construction. The pair-fit step picks the best-cointegrated pair on a *survivor* universe. **Discount any OOS Sharpe by ~30%+ when generalizing forward.** Only the holdout tells anything close to truth, and even that is survivor-biased.

**Ported infrastructure (from pairs_bot):**
- Log-price OLS regression for β/α.
- Wilder-style half-life filter (`phi ≤ 0` → 0.5 fast-MR; `phi ≥ 1` → ∞ rejected).
- EWMA z-score on spread.
- ADF cointegration filter (statsmodels.adfuller, p < 0.05).
- All magic numbers exposed in `DEFAULT_PARAMS` / `PARAM_SPACE`.

**Sizing.** `RAW_SIZING=True`, `MAX_POSITION=1.0`. `top_n_pairs=5`, `leg_size=0.5` → gross per leg ~5%, total gross 50%.

**What was NOT ported (future iteration candidates):**
- Kalman β/α refit (adaptive vs periodic OLS).
- KPSS confirmation filter (off in pairs_bot by default).
- Hurst R/S filter (untested in pairs_bot).
- BH FDR correction across pair candidates — **important** for multiple-testing honesty given the candidate-pair scan count.
- Vol-targeted leg sizing (`leg_a_notional = RISK_USD / σ_spread`).
- Multi-TF gating (1h trigger + 4h regime confirm).
- Cost-aware skip when `z_entry · σ < commission`.
- Drop-and-replace mid-trade on cointegration breakdown.

**Key lesson.** Pairs is structurally different from the directional strategies: results are dominated by **pair selection quality**, not signal tuning. **BH FDR correction across candidate pairs** is the single most important honest-research add-on.

---

## 12. pairs_trading — cointegration-ranked basket on top-50

**Thesis.** Variant of pairs: weekly scan of all C(50,2)=1225 pairs in top-50 universe, rank by AR(1) half-life of OLS residual (Engle-Granger style), trade top-K mean-reverting pairs next week.

**Status: methodology pivot mid-program; surfaced a harness interaction bug.**

**Root cause of `0 OOS trades` (deep diagnostic) — preserve this insight:**

**Layer 1 — `--lookback 60D` padding + stacked positions interaction.** `runner.iterate` defaults `--lookback "60D"`. The harness loads data starting 60 days before each WF window's `train_start`, then zeros target positions during the padding period. With overlapping pairs sharing symbols (BTC in multiple pairs), vectorbt sees a 0→non-zero jump at `train_start` and records ONE long-running round-trip per symbol with `entry_time = train_start` — which is TRAIN, not OOS. W0 escapes this because its padding has no data (pre-2024-01-01). **Fix: `--lookback "0"` for pairs strategies.**

**Layer 2 — cointegration regime breakdown.** Even with `--lookback "0"` AND greedy non-overlapping pair selection, W2 (~Apr-Jun 2025) and W3 (~Nov 2025 - Jan 2026) OOS slices still produce 0 trades. **This is genuine** — `_score_pairs` returns 0 valid pairs because rolling-residual AR(1) falls outside (0, 1) during cycle-peak / cycle-crash regimes. **The strategy correctly stays flat when no pairs are stationary.**

The harness `min_trades=50 → composite=-∞` rule punishes this as failure. For **regime-aware mean-reversion strategies, this rule is too coarse** — going flat when cointegration breaks is the RIGHT thing to do.

**Best methodology (per real edge, not composite): iter 28 — non-overlapping greedy top-5 by AR(1) half-life, scheduled entry on residual sign at refit boundaries.** W0 +6.23 Sharpe (44 trades), W1 +4.60 Sharpe (54 trades), W2 0 (correct silence), W3 -1.83 (carryover loss). Aggregate composite -∞ because W2/W3 fail the gate.

**Open angles:**
- Manual holdout on iter-28-style methodology — bypasses WF gate.
- CPCV / single-OOS evaluation — `harness/cpcv.py` exists.
- More lenient cointegration criterion (Hurst < 0.45) admitting more candidates in regime transitions.
- Pair selection robustness — extend pair-score lookback (2-3 months) for better signal in regime transitions.

**Key lesson.** **Harness's per-window `min_trades` gate is the wrong filter for regime-aware MR strategies.** A strategy that correctly refuses to trade in unfavorable regimes should not be scored -∞. Possible refinement: "if a WF window's OOS slice has 0 entries AND 0 DD AND 0 Sharpe, treat as 'no engagement' and exclude from aggregation". This is opinionated and worth raising explicitly before redoing pairs.

---

# Cross-cutting primitives (what worked across multiple strategies)

These survived in 2+ strategies and are worth trying first on any new strategy:

1. **Multi-symbol diversification** — the single biggest improvement in `keltner` (5→10), `bb_rsi_meanrev` (1→10), `mom_xsection` (10→45). Always try expanding the universe before tuning parameters.
2. **HTF trend gate** (1d EMA50 / EMA100, weekly EMA slope) — load-bearing in `keltner`, `bb_rsi_meanrev`, `trend_supertrend`.
3. **Long-only on perps when funding is structural** — `bb_rsi_meanrev`. Shorts pay funding on long-bias regimes; in MR especially this is structural drag.
4. **Asymmetric long/short parameters** — `mom_tsmom` (asymmetric lookback), `trend_supertrend` (asymmetric multiplier). Crypto long-bias makes symmetric shorts structurally worse.
5. **Continuous rebalance (hold=1) + vol-target per leg** — `mom_xsection`. Solves "strategy flat most of the time" problem while equalizing risk.
6. **`lookback ≤ OOS_window / 2`** — hard rule. Longer lookbacks eat the OOS slice as warmup, inflating Sharpe on a small effective sample.

# Cross-cutting anti-patterns (always burn)

1. **12-1 skip-week / month rule** — equity-momentum classic that **does not transfer to crypto**. Refuted in `mom_tsmom`, `mom_xsection` (multiple universes).
2. **Vol-normalized ranking** (z-score of return instead of raw return) — kills edge on crypto majors. High-vol IS high-momentum here; the divisor destroys useful information.
3. **Stacking 4+ filters** — Sharpe inflates from variance collapse as `pct_time_in_position` falls; total return goes to zero; max DD looks great because you're in cash. The framework now penalizes `pct_time_in_position < 20%` directly because of `trend_supertrend`'s gaming pattern (composite +1.05 with OOS return 2.5% on 65% time-in-position — borderline; would be catastrophic if filters had pushed TIP below 20%).
4. **Magnitude / hard-threshold filters on existing trend signals** — destroy edge by cutting marginal-but-correct signals (`mom_tsmom`, `trend_supertrend` repeated).
5. **Symmetric shorts in MR on perps** — funding drag eats the short leg structurally.
6. **`long_only=1` on trend-following crypto baskets** — kills W3 cycle-reversal edge (`trend_supertrend`, `keltner` basket variant).
7. **Per-symbol regime normalization** (ADX vs own median/quantile) when basket-uniform threshold works — `trend_supertrend` 4 consecutive REVERTs.
8. **Cherry-picking universe** to fix one window — `mom_tsmom` (top-3 majors, 8 trades), refuted multiple times.
9. **Long lookback for short OOS slice** — `mom_xsection` lookback=180 ate first half of every OOS window as warmup, inflating Sharpe on small effective sample.
10. **Iter-tuning until OOS looks good** — that IS using OOS as train. Watch DSR drift across iters as the canary.

# Framework lessons for future research

1. **`pct_time_in_position` is the canary** for Sharpe-inflation-via-inactivity. Now penalized in composite (`min_time_in_position=20%`, linear penalty up to 1.0 at 0%) and flagged in diagnostics.
2. **`stitched.compounded_return_pct`** is the reality check for "great Sharpe, where's the money?". Always cross-check.
3. **CPCV ≠ regime-shift robustness.** Both CPCV and DSR evaluate within the train+val distribution. A pure momentum strategy can have CPCV median +1.15, DSR 0.86, and still crash -2.65 Sharpe on a holdout that crosses a regime boundary (`xs_momentum`). Plan for ~30-50% give-back on regime-shifting forward periods.
4. **`min_trades=50` per WF window is too coarse for regime-aware MR.** A strategy that correctly stays flat when cointegration / dispersion breaks should not be auto-penalized to -∞. (`pairs_trading`.)
5. **`--lookback 60D` default + stacked positions = phantom train trades.** Pairs and any strategy with overlapping per-symbol exposure need `--lookback "0"` to avoid TRAIN-attributed long-running round-trips from the padding period.
6. **Holdout is one shot.** `xs_momentum` is forever holdout-spent on its 2026 window. Plan the holdout decision carefully.

# Untried directions (parked, not refuted)

- **Funding-rate-as-signal** across most strategies (filter, sizing, regime). Mentioned in `METHODS.md §2`. Tried only as a coarse sign-gate; not as a graded signal.
- **HMM regime classification** (model-based, not threshold) — `keltner_regime_switch` negative result on ADX-tercile suggests trying a richer classifier.
- **Hurst exponent regime classifier** (H > 0.5 = trending, H < 0.5 = MR).
- **BBW (Bollinger band width) as regime signal.**
- **Pairs with FDR correction across candidate pairs** — `pairs` open item; the most important honest-research addition.
- **Multi-strategy portfolio** — `xs_momentum` open item: combining market-neutral long-only with a directional strategy may compound to portfolio Sharpe > 2 via low correlation.
- **Mid-cap universe extension (60-80 coins)** for XS strategies — `mom_xsection` parked open angle.
- **Meta-labeling on a positive-edge primary** (`mr_zscore_meta` scaffold applied to e.g. `bb_rsi_meanrev` champion or `trend_supertrend` champion).
- **Kalman β/α refit on pairs.**
- **Drop-and-replace on pairs when cointegration breaks mid-trade.**
- **4h timeframe for XS strategies** with proportional lookback (`mom_xsection`).
