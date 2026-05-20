# xs_mr_alts — research log

## Slot
Cross-sectional mean-reversion (XS-MR). Market-neutral basket strategy.
Sibling to per-symbol MR (`zscore_mr_alts`), opposite-sign cousin to
XS-momentum.

## Baseline thesis
The prior per-symbol MR (`zscore_mr_alts`) hit PF≈1.0 because every fade
competes with the basket's overall direction: when the basket is going down,
a long-fade at z<-2 is fighting beta, not catching micro-reversion.

Cross-sectional ranking neutralizes beta by construction:
1. Lookback return `r_i = close_i / close_i[-N] - 1` for each symbol.
2. Residual `r_i - mean(r_i)` — strip the basket-wide move.
3. Rank residuals at each bar.
4. Long bottom `q` of names, short top `q`. Equal weight per active position.
5. Gross exposure = 100%, net ≈ 0%.

The "edge" we're betting on: short-term residual returns mean-revert because
the relative ordering of crypto alts shuffles faster than fundamentals warrant.

## Universe
Same 24 mid-cap alts as zscore_mr_alts (DOGE, AVAX, LINK, DOT, TRX, BCH,
NEAR, ATOM, XLM, OP, INJ, SUI, TIA, SEI, UNI, FIL, HBAR, ICP, LDO, CRV,
SAND, AXS, IMX, ETC). All with full 24-month coverage.

## Caveats noted up-front
- **Survivorship bias is sharper for XS than for single-symbol.** The
  basket is currently-listed. Alts that delisted between 2024 and 2026
  (and would have been ranked-against in real time) are missing. The
  remaining names skew toward "winners". Discount XS Sharpe by 20-30%
  vs what the backtest shows.
- **Costs.** Default rebalances every bar (15m). 10 active positions ×
  full turnover per bar × 5.5bp taker = ~5.5bp/bar drag worst case.
  The signal-smoothness from a 6h lookback should naturally keep churn
  much lower than that worst case, but verify n_trades on iter 1.
- **No funding data** for most of these symbols on disk. Net exposure ≈ 0
  by construction so funding drag is roughly self-canceling — better
  starting position than the long-biased per-symbol strategy.

## Planned iteration directions (priority order)
1. **Baseline** — lookback=24 (6h), q=0.2 (5 long / 5 short). Confirm no
   LOOKAHEAD_BUG, measure raw turnover.
2. Lookback sweep — 6h is a guess; try 1h / 12h / 24h. The shape of the
   return-windows-matter curve is informative.
3. Quantile sweep — q=0.1 (more selective, 2-3 per side) vs q=0.3 (broader).
4. Holding-period gate — rebalance every K bars instead of every bar to cut
   churn if turnover is too high.
5. Hysteresis bands — wide-keep/narrow-enter — enter at top/bottom 20%,
   exit only when leaving top/bottom 30%.
6. Long-only variant — given crypto net-bullish + survivorship bias, longs
   may carry. (Test cautiously — iter 10 of zscore_mr_alts showed this
   helps stitched equity but hurts composite via lower TiP.)
7. Vol-normalized residuals — divide residual by symbol's ATR%, so we
   rank "moves in units of own vol" not "moves in absolute return".
8. Add 4h trend-regime gate (if iter 1-3 leave bear-bucket leakage).

## Iteration log

| # | Verdict | Composite | OOS Sharpe | MaxDD | n_trades | TiP% | TotalRet | PF | Note |
|---|---------|-----------|------------|-------|----------|------|----------|----|------|
| 1 | BASELINE | -37.31 | -32.88 | 68% | 7845 | 100% | -65% | 0.71 | rebal-every-bar, lookback=24 (6h), q=0.2 — 128k trades crush via cost wall |
| 2 | KEEP | -5.49 | -4.60 | 21% | 1368 | 100% | -13% | 0.89 | +rebal_bars=24 (6h hold) — churn dropped 24x but signal still noise: payoff 0.89, win 50% |
| 3 | REVERT | -9.96 | -8.28 | 26% | 1368 | 100% | -21% | 0.86 | flip to XS-momentum at 6h — worse than MR; signal pure noise at 6h regardless of direction |
| 4 | KEEP | -2.44 | -1.35 | 19% | 728 | 100% | -5% | 0.91 | lookback 24→96 (24h MR) — direction right, but still net negative |
| 5 | REVERT | -3.37 | -1.86 | 27% | 537 | 100% | -8% | 0.92 | lookback 96→192 (48h) — past peak; 24h optimal |
| 6 | KEEP | -2.31 | -1.22 | 11% | 405 | **29%** | -3% | 0.95 | +basket 4h regime gate (q=0.3) — XS has same bull-bleed as per-symbol MR; gate helps MaxDD but PF stuck |
| 7 | REVERT | -7.99 | -6.04 | 12% | 405 | 29% | -8% | 0.87 | flip to XS-momentum at 24h + gate — definitively wrong sign |
| 8 | KEEP | -2.04 | -1.21 | 11% | 401 | 29% | -2% | 0.95 | +vol-normalize residuals (rank by residual/ATR%) — marginal (Δ +0.27); cuts ranking bias toward high-vol names |

**Current best**: iter 8 (composite -2.04, Sharpe -1.21, PF 0.95).

## What's been ruled out

The XS-MR hypothesis FAILED. Across 8 iterations:

- **Never reached PF > 1.0.** Best PF was 0.95 at iter 6/8. Costs are not the
  only problem — the signal itself has no edge.
- **Both signs of the signal are losers** (iter 3 momentum at 6h, iter 7 mom
  at 24h). The basket residual ranking is not a tradeable feature on this
  universe at any horizon tested (6h, 24h, 48h).
- **Regime decomposition is the same shape as per-symbol MR**: works in flat,
  bleeds in bull/bear. The cross-sectional hedge removes basket beta but
  not idiosyncratic trend-persistence inside the cohorts. Original
  hypothesis ("XS solves regime problem") empirically refuted.
- **vs prior strategy:** zscore_mr_alts (per-symbol MR with regime+vol filters)
  reached composite -0.13 / Sharpe +0.52 / PF 1.04. XS best is composite
  -2.04 / Sharpe -1.21 / PF 0.95. **Cross-sectional architecture is strictly
  worse on this universe.**

Possible reasons XS-MR fails on crypto mid-cap perps where it works on equities:
1. **Universe too small.** 24 symbols → top/bottom quintile = 5 names per side.
   With Q=0.2 ranking is noisy; literature usually wants 100-500 names.
2. **Sector co-movement.** Mid-cap alts cluster (memes move together, L1s
   move together) so the "basket" isn't a clean benchmark.
3. **Survivorship bias** makes the basket return artificially smooth and
   the residuals artificially small.
4. **Lookback / cost mismatch.** True XS signals on crypto may need ≥7 day
   horizons (literature) but at that point rebalance frequency is so low
   the strategy barely trades.

## Recommendation

Pause this strategy at iter 8. Do not iterate further within XS-MR — DSR is
zero across the run, indicating no statistically defensible best emerged.

Next directions worth real effort:
1. **Return-based z** on per-symbol (NOT XS) — keep the working
   zscore_mr_alts architecture but replace price-z with return-z.
2. **Connors RSI(2)** on per-symbol — different signal architecture,
   established literature.
3. **Larger universe XS** — if data layer can support 60-100 perps, the
   XS architecture may finally have enough breadth to work. Today's 24
   names are below the literature's typical N.
