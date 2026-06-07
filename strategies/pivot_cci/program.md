# Pivot Points Mean Reversion with CCI

## Thesis
Mean reversion strategy using Daily Pivot Points as support/resistance levels and CCI (Commodity Channel Index) as a momentum/overbought/oversold filter.

## Logic
- **Pivots**: Standard daily pivots calculated from previous day's OHLC.
  - $P = (H + L + C) / 3$
  - $S1 = 2P - H$, $R1 = 2P - L$
  - $S2 = P - (H - L)$, $R2 = P + (H - L)$
- **CCI**: 20-period CCI on the trading timeframe.
- **Entry Long**: 
  - Price is near or below $S1$ or $S2$.
  - $CCI < -100$ and starts rising.
- **Entry Short**:
  - Price is near or above $R1$ or $R2$.
  - $CCI > 100$ and starts falling.
- **Exit**:
  - Long: Price reaches $P$ or $CCI > 100$.
  - Short: Price reaches $P$ or $CCI < -100$.

## Results (Baseline)
- **Period**: 2024-01-01 to 2024-04-01
- **Train Sharpe**: -1.29
- **OOS Sharpe**: 4.33
- **N Trades**: 101 (74 Train + 27 OOS)
- **Max Drawdown**: 20.6% (Train), 8.7% (OOS)

## Observations
- High variance between Train and OOS periods.
- Significant time in position (~64%).
- Profit factor in OOS is 2.41, but in Train is 0.77.

## Current best (WF=4, 2024-01 → 2026-01, BTC/ETH)
- composite **1.0237**, OOS Sharpe **2.20**, DSR 0.94, oos_n_trades 12/window
  (189 total stitched), profit factor 1.86, 18 green / 6 red months.
- **Binding constraint is Sharpe, not trade count.** `pct_time_in_position ≈ 10%`
  → activity penalty active, but composite is capped by the regime profile below.

## Regime profile (consistent across iters)
- **Earns in flat/chop regimes, bleeds in trends** in the per-bucket view.
  BUT this is a *consequence* of the directional gate, not a defect of it —
  see iter 4. The directional EMA200 gate is **load-bearing**.

## Iteration log
| iter | verdict | composite | note |
|------|---------|-----------|------|
| 1 | BASELINE | 1.0237 | original pivot_cci (Pivot+CCI+RSI+funding+EMA200), BTC/ETH |
| 2 | REVERT | 1.0237 | restore from best_strategy.py (strategy.py had been overwritten by an unrelated z-score MR strat) — reproduces baseline bit-for-bit |
| 3 | REVERT | 1.0111 | **universe BTC/ETH → 8 symbols.** Trades ↑ (12→38/win, 189→603, time-in-pos 10%→21%) but edge diluted: OOS Sharpe 2.20→1.84, full-period PF collapsed to 1.17, expectancy $25→$5, largest trade = 59% PnL. Alts add noise, not edge. |
| 4 | REVERT | -2.2877 | **flat-regime gate instead of directional EMA200.** Catastrophic: OOS Sharpe -0.27, PF 0.73, stitched -58%, 17 red months. Refuted the "flat=edge, drop directionality" reading. |
| 5 | REVERT | 0.0046 | **multi: univ→8 + drop RSI + ATR(14)×2.5 stop.** Composite collapsed to ~0 (vs univ-only 1.011 at iter3), PF 1.01, expectancy $0.12. The ATR stop is the culprit — see ruled-out. RSI not pure dead-weight either. |
| 6 | REVERT | 0.8666 | **univ→8 + volatility-normalized sizing (pos × clip(target_vol/ATR%,0,1)).** Risk profile genuinely improved (max DD 2.3%, worst month −2.0%, longest red streak 2mo, CVaR halved vs baseline, concentration 59%→49%) but composite still < baseline (Sharpe 1.58, PF 1.14). Sizing can't manufacture alt edge. |
| 7 | REVERT | 0.0352 | **univ→8 + trailing risk-adjusted self-weighting (clip(rolling-720 annualized Sharpe / target,0,1)).** Composite collapsed (Sharpe 0.84, activity 16%). Per-symbol performance is NOT persistent + sparse PnL makes rolling Sharpe ≈ noise → weights cut exposure at wrong times. Performance-chasing refuted. |
| 8 | REVERT | 0.7166 | **univ→20 a-priori liquid majors, equal-weight.** Diversification did NOT help: OOS Sharpe flat at 1.81 (= 8-sym 1.84) while max DD WORSENED 3.0%→7.3%. More names lower avg per-symbol edge as fast as the √(n/(1+(n-1)ρ)) multiplier helps, and crypto corr spikes in stress (unconditional PnL corr 0.135 is illusory in drawdowns — all alts dump together). |
| 9 | REVERT | 0.9313 | **time-stop (max_hold_bars=24) on BTC/ETH baseline.** Near-no-op (n_trades 12, time-in-pos 9.8% unchanged) — but NOT because BTC/ETH lacks a long-drag tail (it has one: q4 −1.02% @17% win). The threshold 24 was simply too loose: avg hold 9.9 bars, q4 losses start ~14 bars, so 24 rarely fired. Threshold error, not absence of pathology. |
| 10 | REVERT | 0.654 | **clean-8 broad universe + time-stop=24.** OOS Sharpe 1.25, max DD 5.9%. Confounded (universe + stop both changed vs iter3) and too-loose stop again; no improvement. |
| 11 | **KEEP** | **1.2202** | **faster CCI exit (exit when CCI recovers past −cci_exit=40 instead of 0), BTC/ETH.** BEAT champion +0.197. OOS Sharpe 2.20→2.54, PF(OOS) 2.46→2.91, payoff 0.97→1.11, max DD 3.04→2.89%, worst month −2.41→−1.98%, all 4 windows OOS+. The diagnostic was right: the slow CCI→0 exit was bleeding the bounce. Trade-off: smaller median win (0.86→0.54%), time-in-pos 10→7.7% (low-activity flag active, but total_return 2.55%/win & 194 trades → not metric-gaming). **NEW CHAMPION.** |
| 12 | REVERT | 0.3151 | **faster CCI exit applied to broad clean-8.** OOS Sharpe 1.17 — far below champion 2.54. The exit fix is a per-symbol win but does NOT rescue breadth: portfolio dilution dominates. Confirms (again) BTC/ETH focus; broad universe definitively closed. |
| 13 | REVERT | 0.2625 | **cci_exit 40→60 (exit even earlier), BTC/ETH.** Worse — windows [3.46, −0.11, 0.19, 4.29], dispersion up, time-in-pos 6%. Brackets the optimum: composite 0(=1.024) < 40(=1.22) > 60(=0.26). cci_exit=40 is an interior peak, beats both neighbours → robust, not a knife-edge. |

## Ruled out
- **Wholesale universe expansion (BTC/ETH → 8 liquid alts).** Raises trade
  count as intended but dilutes the BTC/ETH edge (PF 1.86 → 1.17). Trade count
  is not the binding constraint; Sharpe is. Do not cherry-pick the "best" alts
  by OOS (selection bias). If revisiting the universe, justify per-symbol on
  structure (clean daily pivots), not on backtest P&L.
- **Dropping the directional EMA200 gate (flat-regime gate instead).** The
  directional gate is the core edge ("buy support dips only in uptrend / sell
  resistance rips only in downtrend"). Without it the strategy catches falling
  knives — trend buckets blow out to −8…−11 Sharpe. Directionality is
  non-negotiable; do not weaken it.
- **Fixed protective stop-loss (ATR×k).** Antithetical to this MR strategy: a
  fixed adverse stop fires on normal pre-reversion noise before price returns
  to P, converting would-be winners into realized losses (PF 1.17 → 1.01,
  largest-trade 59% → 208%). The exits (revert-to-P / CCI-zero-cross) ARE the
  MR risk management. Do not add adverse-excursion stops. (Time-stop untested.)

## Root cause of broad-universe underperformance (diagnostic, NOT a tuning miss)
Read-only per-symbol study over 2024–2026 (scripts in temp, not committed):
- **MR edge generalizes.** Intrinsic mean-reversion is as strong or stronger on
  alts (SOL VR24 0.86 — most MR; ETH weakest at 1.02). Pivot bounce is real on
  alts (fwd +6h after S1 pierce: SUI +0.24%, SOL +0.12% vs BTC +0.06%).
- **Every symbol is standalone-profitable** running the real signal: net return
  ETH +34.5% / SUI +33.8% / XRP +20.3% / PEPE +15.6% / DOGE +12.8% / BTC +12.1%
  / SOL +2.5%. So the earlier "edge is BTC/ETH-specific" reading was WRONG.
- **The drop is a portfolio-construction artifact.** Standalone Sharpe: ETH 1.66,
  BTC 1.00, SUI 0.94, XRP 0.83, DOGE 0.56, PEPE 0.45, SOL 0.25. PnL streams are
  nearly uncorrelated (mean pairwise corr **0.135** — diversification IS
  available). But equal-weight blended Sharpe falls {BTC,ETH}=1.75 → all-7=1.45.
  Incremental by descending Sharpe peaks at {ETH,BTC,SUI,XRP}=**1.77**, then the
  low-Sharpe names (DOGE/PEPE/SOL) drag it down. High alt vol isn't matched by
  proportionally higher per-trade return → equal weight over-allocates to them.
- **Why iter-6 vol-sizing didn't fix it:** it penalizes volatility blindly, so it
  also downweighted SUI (high vol BUT Sharpe 0.94). The thing to downweight is
  low *return-per-unit-risk*, not raw vol.
- CAVEAT: the in-sample "top-4 by Sharpe" is selection bias — NOT a tradeable
  symbol list. The fix must be a causal rule, not cherry-picking.

## EXIT diagnostic (read-only, 8 liquid symbols, 714 trades)
- **We keep only 21% of the favorable move.** avg realized +0.26% vs avg MFE
  +1.26% (capture 0.21); ~1.0% given back per trade. fwd6 after exit −0.31% →
  we do NOT exit too early; we ride to MFE then give it back before exit fires.
- **By exit reason:** CCI-zero-cross = 60.6% of exits but capture only **0.11**
  (slow/lagging); P-touch = 21.4% of exits, capture **0.82** (the good exits);
  opp/other = 17.9%, realized −0.94% (the losers).
- **By hold time:** q1(short) +1.54% @94% win → q4(long) **−2.07% @13% win.**
  Failed reversions bleed.
- **BTC/ETH-only re-check (189 trades):** the same pathology exists on the
  champion, just milder — capture **0.47**, CCI0 = 60% of exits @ capture 0.37
  (vs P-touch 0.94), q4 hold = **−1.02% @17% win**. So exit fixes ARE a valid
  champion lever (correcting the iter-9 misread). iter 9's 24-bar time-stop
  barely fired only because avg hold is 9.9 bars and q4 losses start ~14 bars —
  24 was too loose. The mechanism behind both the low capture AND the long-drag
  is the **slow CCI-zero exit**: waiting for CCI to crawl to 0 lets price
  reverse first. Faster CCI exit is the targeted fix.

## Universe-breadth thread CLOSED (fully mapped: 2 / 8 / 20 symbols)
- Composite by breadth: BTC/ETH **1.024** > 8-sym 1.011 > 20-sym 0.717.
  OOS Sharpe is flat ~1.8 for 8 and 20 symbols; only max DD changes (worse with
  20). Adding breadth does NOT raise risk-adjusted return for this strategy.
- Why diversification fails: (a) avg per-symbol edge drops as fast as the
  √(n/(1+(n-1)ρ)) multiplier rises; (b) the 0.135 unconditional PnL correlation
  spikes in stress — alt losses cluster, so DD grows, not shrinks, with n.
- Sweet spot for "trade the market" = ~8 equal-weight, composite ≈ baseline.
  Beyond that, breadth hurts. Do not test more universe variants on this window.

## Sizing thread CLOSED (both approaches failed)
- iter 6 (inverse-vol) and iter 7 (trailing Sharpe) both REVERTed. The dilution
  by low-RAR names is real, but it is **not exploitable out-of-sample**: which
  symbols are low-RAR is not predictable from trailing data (performance isn't
  persistent), and inverse-vol mis-penalizes high-vol-high-edge names (SUI).
- The in-sample peak {ETH,BTC,SUI,XRP}=1.77 is selection bias — not tradeable.
- **Conclusion for the "trade the market" goal:** equal-weight 8-symbol (iter 3,
  composite 1.011) is statistically indistinguishable from the BTC/ETH baseline
  (1.024) — same risk-adjusted performance, 4× the trades, far more diversified.
  It is the better *deployable* market version even though the composite gate
  keeps BTC/ETH as nominal champion by a noise-level 0.013.
- Further work should target the per-trade edge (e.g. exits) — which lifts all
  symbols incl. baseline — rather than more portfolio reshaping.

## Position contract
- harness/backtest.py: `position` = target weight, clipped to [-MAX_POSITION, +1],
  `size = position / n_symbols` (targetpercent). Fractional positions work.

## On DSR interpretation (corrected)
- The per-verdict `dsr` is the Deflated Sharpe of THAT candidate; it is low for
  weak candidates (low Sharpe / low activity), e.g. iter10 0.41 — NOT a counter
  of "accumulated search overfitting", and not a danger-zone gate on future
  KEEPs. A genuinely strong candidate will have its own high DSR regardless of
  how many weak variants were reverted before it.
- The real multiple-testing risk only bites if we try many random variants and
  keep the lucky winner. We test motivated hypotheses and revert most, judging
  each on its own OOS consistency + DSR — that is the correct guard, not a hard
  iteration cap.
