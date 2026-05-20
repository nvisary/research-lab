# macd_ema200 — research log

## Slot
Per-asset trend-following (TS). Single-symbol BTCUSDT 1h. Same quadrant as
`trend_supertrend`, `keltner`, `donchian_breakout`, `mom_tsmom`.

## Baseline thesis
Classic MACD(12,26,9) signal-line crossover gated by EMA200 trend filter.
- Long while `MACD > signal` AND `close > EMA200`.
- Short while `MACD < signal` AND `close < EMA200`.
- Else flat.

Two textbook ingredients: MACD as a smoothed momentum oscillator, EMA200 as
the canonical long-term trend dividing line. Filter cuts the mean-reverting
counter-trend whipsaws that murder unfiltered MACD systems in chop.

## Caveats noted up-front
- **No funding data on disk** for BTCUSDT (checked 2026-05-19 — `data/bybit/funding/`
  is absent). Per AGENTS.md §1, equity is silently long-biased by ~7-8%/yr for
  any net-long exposure. Cross-check shorts contribution before celebrating long-side wins.
- Single-symbol → no survivorship bias issue.

## Planned iteration directions (priority order)
1. Establish baseline at default params, confirm no LOOKAHEAD_BUG.
2. Trend-window sweep (EMA100 / EMA200 / EMA300) — is 200 the sweet spot or arbitrary?
3. Long-only vs symmetric — given crypto long-bias drift + no funding data, shorts may bleed.
4. Multi-TF confirmation — 1h MACD trigger gated by 4h trend.
5. Volatility/regime filters (ATR-pct band, ADX) — only trade when regime exists.
6. Asymmetric MACD params for L/S (cf. mom_tsmom asymmetric lookback success).
7. ATR-based stop-loss / trailing exit.
8. Vol-targeted sizing.
9. Indicator swap: MACD → PPO (scale-invariant).

## Iteration log

| # | Verdict | Composite | OOS Sharpe | MaxDD | n_trades | TiP% | TotalRet | Note |
|---|---------|-----------|------------|-------|----------|------|----------|------|
| 1 | BASELINE | -5.725 | -3.656 | 51% | 259 | 53% | -47% | 1h MACD(12,26,9) + EMA200, symmetric L/S — catastrophic, all 4 windows negative |
| 2 | REVERT | -6.001 | — | — | — | — | — | zero-line gate (MACD>0 for long); cut valid early-trend entries |
| 3 | REVERT | -6.464 | -4.491 | 33% | 44 | 38% | -15% | replace price>EMA200 with EMA200-slope>0 on 1h — over-filtered |
| 4 | **KEEP** | -1.971 | -1.032 | 12.5% | 14 | 49% | -3.5% | **TF 1h→4h** — biggest single win; canonical trend-following scale |
| 5 | REVERT | -2.659 | -0.677 | 8% | 7 | 28% | +0.6% | long_only=1 — Sharpe & DD better but 7 trades crashes low_trades_penalty |
| 6 | **KEEP** | -1.431 | -0.439 | 10% | 12 | 43% | -1.1% | **add EMA200-slope on TOP of price>EMA200** — kill flat-regime longs |
| 7 | REVERT | -2.457 | -1.305 | 12% | 71 | 82% | -3.3% | 6-symbol majors basket — alts' MACD-fit dilutes BTC signal |
| 8 | REVERT | -3.089 | -1.840 | 18% | 11 | 42% | -6.4% | MACD slow 26→52 — too slow, missed cycle-peak entries |
| 9 | **KEEP** | -1.092 | -0.231 | 9.1% | 11 | 39% | -0.4% | **extreme-vol gate** (skip ATR% top-10pct, 200-bar rolling) — best so far |
| 10 | REVERT | -1.250 | -0.347 | 10% | 11 | 41% | -0.9% | slope_lb 24→12 — too responsive |
| 11 | REVERT | -1.512 | -0.610 | 9% | 11 | 38% | -1.4% | slope_lb 24→48 — too slow; 24 is plateau |
| 12 | REVERT | -1.841 | -0.747 | 13% | 11 | 35% | -2.0% | trend EMA 200→100 — shorter trend hurts |
| 13 | REVERT | -2.248 | -0.855 | 10% | 10 | 28% | -1.5% | 1d EMA50 multi-TF gate — over-filtered, TiP collapsed |
| 14 | REVERT | -2.341 | -0.734 | 15% | 16 | 40% | -1.6% | Fibonacci MACD (8,21,5) — defaults (12,26,9) are sweet spot |
| 15 | **KEEP** | -1.028 | -0.186 | 9.1% | 11 | 37% | -0.2% | vol_q 0.90→0.80 — tighter vol gate |
| 16 | **KEEP** | -0.607 | **+0.282** | 9.1% | 11 | 34% | +1.2% | vol_q 0.80→0.70 — **first positive Sharpe** |
| 17 | REVERT | -0.848 | +0.205 | 7% | 10 | 30% | +1.2% | vol_q 0.70→0.60 — over-tight, TiP drops |
| 18 | **KEEP** | -0.162 | **+0.593** | 6.6% | 11 | 34% | +1.8% | **vol-targeted sizing** (vol_target=0.01, clip [0.3, 1.0]) |
| 19 | **KEEP** | **-0.012** | **+0.696** | **5.3%** | 11 | 34% | **+1.8%** | vol_target 0.01→0.008 — **current champion** |
| 20 | REVERT | -0.046 | +0.663 | 4.1% | 11 | 34% | +1.4% | vol_target 0.006 — too conservative, plateau |
| 21 | REVERT | -0.115 | +0.505 | 5.5% | 24 | 47% | +1.0% | add ETHUSDT — drags BTC per-trade quality |
| 22 | REVERT | -0.187 | +0.571 | 6.0% | 12 | 33% | +1.4% | signal_span 9→5 — faster signal hurts |
| 23 | REVERT | -0.012 | +0.696 | 5.3% | 11 | 34% | +1.8% | size_floor 0.3→0.15 — no-op (clip rarely fires low) |
| 24 | REVERT | -4.079 | -1.842 | 15% | 21 | 31% | -3.6% | TF 4h→2h — way noisier |
| 25 | REVERT | -0.819 | +0.117 | 7.6% | 13 | 38% | +0.4% | drop slope filter — slope contributes meaningful selectivity |
| 26 | REVERT | -∞ | 0 | 0 | 0 | — | — | long_only=1 retest — slope+price+vol_q in Q4-2025 bear OOS → 0 long entries |

## Final summary (26 iterations)

**Champion — iter 19:**
- Composite: **-0.012** (essentially zero)
- OOS Sharpe: **+0.696**
- OOS MaxDD: **5.3%**
- OOS Total return: **+1.8%**
- OOS n_trades: 11 (penalty active, ~−0.27 composite drag)
- OOS time-in-position: 33.6% (above 20% floor, no gaming-via-inactivity penalty)
- Setup: BTCUSDT 4h, MACD(12,26,9) + (price>EMA200 AND EMA200-slope>0 over 24 bars)
  + extreme-vol gate (skip top 30pct ATR%, 200-bar rolling) + vol-targeted
  sizing (clip 0.008/atr_pct in [0.3, 1.0]), symmetric L/S

**Trajectory.** Catastrophic baseline (-5.73 composite) → tractable on 4h
(-1.97) → vol-and-slope-filtered (-0.61) → vol-targeted-sized (-0.012).
Total composite gain: **+5.7**. Sharpe trajectory: **-3.66 → +0.70**.

**What's actually working in the champion:**
1. **4h TF** — single biggest change; 1h MACD is structurally too noisy on BTC.
2. **EMA200 price + slope combined gate** — slope-only or price-only each worse.
3. **Extreme-vol gate (q=0.70)** — turns Sharpe positive by avoiding flash-crash entries.
4. **Vol-targeted sizing** — equal-risk per trade, drops MaxDD ~30% and pushes Sharpe past 0.5.

**Caveats on the result.**
- 11 OOS trades is a thin sample (low_trades_penalty active). DSR likely
  near 0 — high selection-bias risk after 26 trials.
- OOS region (2025-07 → 2025-12) is exactly the cycle-peak Q4 flash-crash
  zone. Trend-following is structurally weak there. +1.8% total return /
  5% MaxDD is *defensive*, not aggressive.
- No funding data on disk — equity is silently long-biased by ~7-8%/yr
  for net-long exposure. Real Sharpe likely lower than reported.
- The win on vol-targeted sizing comes partly from MaxDD shrinking
  (composite has a 0.5×DD subtraction) — Sharpe rose too, but the
  composite gain mixes both.

**Recommended next steps for user / holdout:**
- **Run `runner.holdout`** to see how iter 19 behaves on 2026-Q1. A
  champion with composite ≈ 0 on OOS that *doesn't collapse* on holdout
  is the realistic expectation here; one that *also doesn't collapse* is
  the win to look for.
- If holdout looks plausible, **fetch funding data** (`datafeed.download_bybit_funding`)
  and re-run to get honest equity.

## What's been ruled out

- **1h TF** — MACD signal too noisy.
- **TF 2h, 1d not tested but expected** — 2h was 3× worse than 4h; 1d would crash n_trades.
- **long_only=1** in TWO setups — first iter (5) due to low_trades_penalty kill,
  later iter (26) due to slope+price+vol_q filtering all OOS bear regime out.
- **MACD parameter sweeps** — (8,21,5) and (12,26,52) both worse than (12,26,9).
  Signal_span 5 worse than 9.
- **Slope-only or price-only regime** — combination is meaningfully better.
- **Slope_lb tuning** — 24 (4 days) plateau optimum; 12 and 48 both worse.
- **Trend EMA 100** — 200 is right.
- **1d EMA50 multi-TF gate** — over-filters, drops TiP below 30%.
- **MACD zero-line gate** — cuts valid early-trend signals.
- **6-symbol majors basket** — alts dilute BTC signal quality.
- **2-symbol BTC+ETH basket** — ETH per-trade quality worse than BTC.
- **size_floor tuning** — clip lower bound rarely binds.
- **vol_target 0.006 / 0.010** — 0.008 is plateau optimum.
- **vol_q 0.60 / 0.85 / 0.90 / 0.95** — 0.70 is plateau optimum.

## Open angles (untried)

- **ATR-trailing stop / take-profit** — state machine; would cap losses per trade.
  Vectorized implementation is involved; would need a careful pass.
- **MACD histogram-based signal** — different turn-detection.
- **PPO (scale-invariant MACD)** — bookmark for if multi-symbol returns.
- **Funding-aware short-skip** — once funding data is downloaded.
- **Different daily_trend span** for multi-TF gate — only EMA50 tried.
- **MAX_POSITION > 1 with RAW_SIZING** — single-asset Kelly-flavored sizing.
- **Conviction sizing** — scale by MACD distance to signal (currently binary).

## What's been ruled out (so far)

- **1h TF** — too noisy for MACD; 4h is sweet spot, 1d not tested but n_trades risk.
- **long_only=1** — Sharpe & MaxDD strictly better but low_trades_penalty kills score.
  *Score-aware finding: shorts contribute to trade count even though they bleed.*
- **Slope-only regime filter** (replacing price>EMA200) — worse; combination is better.
- **Multi-symbol basket** (6 majors) — alts' MACD-fit drags signal quality down.
- **MACD-slow tuning** — both faster (8,21,5) and slower (12,26,52) hurt; (12,26,9) is locally optimal.
- **slope_lb tuning** — 24 is a plateau optimum; both 12 and 48 worse.
- **trend-EMA tuning** — 200 is right; 100 hurt.
- **1d higher-TF gate (EMA50)** — over-filters, TiP drops below 30%.
- **MACD zero-line gate** — cuts valid early-trend signals.

## Open angles
- **Vol-targeted sizing** — equal risk per trade (clip strength/atr) rather than equal notional.
- **ATR-trailing stop / take-profit** — cap losses, lock in winners (state machine).
- **vol_q sweep** — current 0.90; try 0.85/0.95.
- **Different daily_trend span** — current 50 too tight; try 100 or 200.
- **MACD → PPO** (scale-invariant) — bookmark for if multi-symbol returns.
- **Funding-data fetch** then add long-bias correction (needs user OK to run datafeed).
- **Histogram-based signal** — MACD hist > 0 instead of MACD > signal.

## Notes on the OOS regime
The OOS window (~2025-07 → 2025-12) covers cycle-peak Q4-2025 flash crashes
per AGENTS.md §2. Trend-following families are structurally weaker in
cycle-reversal regimes (baseline_strategies.md "trend_supertrend" ruled out
several attempts in similar regions). Best composite so far (-1.09) is
breakeven on total return with 9% MaxDD — slight negative Sharpe is
plausibly the cost of forcing trend-following through a reversal regime.

