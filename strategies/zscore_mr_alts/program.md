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

All verified to have full 2024-01 → 2026-04 monthly coverage on disk. LTCUSDT
was considered and dropped (only 16 of 28 months available).

## Caveats noted up-front
- **Survivorship bias.** All 24 names are currently-listed Bybit perps. Mid-cap
  alts that delisted between 2024 and 2026 (and would have been in a real-time
  basket) are not represented. Discount XS Sharpe accordingly.
- **No funding parquets on disk** (`data/bybit/funding/` is absent for these
  symbols — confirm). Net-long bias inflates equity by ~funding mean. MR
  strategies tend toward symmetric L/S so impact is smaller than for trend, but
  still real.
- **Costs are the dominant risk.** 5.5 bps taker × frequent reversion entries
  on 24 symbols can easily eat any z-score edge. The harness applies fees;
  watch composite vs gross.
- **15m TF = ~70k bars per symbol × 24 symbols.** Heavy load. If iteration time
  hurts, drop universe to 12 highest-volume names first, not coarser TF.

## Planned iteration directions (priority order)
1. Establish baseline at default params (z=96, k=2.0/0.5), confirm no LOOKAHEAD_BUG.
2. Z-window sweep — is 24h the right baseline window? Try 48 / 96 / 192 / 384 (12h–4d).
3. Entry / exit threshold sweep — does asymmetric `entry_k` for long vs short help?
4. Long-only vs symmetric — given crypto long-bias + no funding data, shorts may bleed.
5. Volatility-band gate — only trade when ATR%/close is in middle quantiles (skip dead chop AND blow-ups).
6. Volume / liquidity gate — skip symbols whose recent volume is in bottom decile of their own history.
7. Cross-sectional ranking — instead of independent z per symbol, rank residual returns and trade top/bottom decile.
8. Higher-TF trend gate — only fade when 4h trend is flat (range regime), skip when 4h is trending.
9. Stop-loss on `|z|` exceeding extreme threshold (catastrophic continuation).

## Iteration log

| # | Verdict | Composite | OOS Sharpe | MaxDD | n_trades | TiP% | TotalRet | DSR | Note |
|---|---------|-----------|------------|-------|----------|------|----------|-----|------|
| 1 | BASELINE | -0.923 | 0.007 | 21% | 1272 | 99% | -0.6% | 0.52 | z=96, k=2.0/0.5 symmetric L/S — Sharpe ~0, regime table shows MR only in flat |
| 2 | KEEP | -0.654 | 0.243 | 17% | 1044 | 96% | +1.6% | 0.41 | +4h trend-gate q=0.5 — modest win, bear/bull buckets ~unchanged |
| 3 | KEEP | -0.139 | 0.406 | 14% | 756 | 92% | +1.5% | **0.61** | regime gate q=0.5→0.3 — biggest jump; PF 1.02; DSR peak |
| 4 | REVERT | -6.68 | -5.02 | 10% | 800 | 38% | -5.7% | 0.00 | regime-exit on flip-out-of-flat — **breaks MR mechanism**, all buckets red |
| 5 | REVERT | -1.20 | -0.17 | 13% | 822 | 91% | +0.1% | 0.02 | z-stop at |z|>4 — also breaks MR, crystallizes losses that would have reverted |
| 6 | KEEP | -0.129 | 0.517 | 12% | 639 | 90% | +1.9% | 0.05 | +vol-floor q=0.2 (skip low-ATR entries) — marginal (Δ=0.01); stitched -47% flag |
| 7 | REVERT | -1.02 | -0.37 | 25% | 327 | 88% | -2.9% | 0.01 | z_window 96→192 — halved trades, flat-bucket Sharpe up but bear-bucket -17 |
| 8 | REVERT | -0.27 | 0.28 | 11% | 450 | 79% | +0.9% | 0.04 | asymmetric short_k=3.0 — bull-buckets healed (-6→0), but trade volume -30%, Sharpe down |
| 9 | REVERT | -1.91 | -0.67 | 15% | 1123 | 90% | -1.4% | 0.01 | z_window 96→48 — confirms 96 is concave optimum; noisier signal |
| 10 | REVERT | -1.12 | -0.20 | 11% | 355 | 58% | -0.2% | 0.03 | long_only=1 — **best stitched -19.9%**, 8/12 buckets healthy, BUT lower TiP kills Sharpe |

Current best: **iter 6** (composite -0.129, regime+vol entry filters, symmetric L/S).
Highest-DSR: iter 3 (still has the strongest statistical defensibility).

## What's been ruled out

After 10 iterations on this hypothesis-family (15m z-score MR on 24 mid-cap
alts + entry filters), the strategy is **structurally PF≈1.0 wall**:

- Two natural defenses (regime-exit, z-stop) **both break the MR mechanism**
  itself — they realize losses on moves that would have reverted. Lesson: do
  not use the same signal-family (z, regime) for both entry and forced exit.
- z_window is concave around 96 (24h baseline). Both 48 and 192 made it worse.
- Asymmetric short threshold and long_only **structurally improve** the equity
  curve, regime decomposition, monthly streaks, and stitched return — but
  hurt the OOS-Sharpe composite because they reduce trade frequency / TiP.
  This is a notable mismatch: by composite, iter 6 wins; by stitched 24-month
  equity (-19.9% vs -47.4%), iter 10 (long_only) wins.
- Win rate is stable around 64%, payoff ratio ~0.5, PF stuck at 0.85-1.04.
  Trade-shape ceiling: ~+0.15% per trade gross, less than 2× round-trip cost.

## Open hypotheses worth trying (NOT yet tested)

Anything below would be a NEW hypothesis-family, not a parameter tweak:

1. **Return-based z** instead of price-z — `z = (logret - mean(logret)) / std(logret)`.
   Decoupled from price drift, may catch reversion more cleanly.
2. **Volume confirmation** at entry — only fade z<-entry_k when volume in
   top quartile of recent (panic-capitulation signature).
3. **Cross-sectional ranking** instead of independent z per symbol — at each
   bar, rank residual returns across the basket, long bottom decile vs short
   top decile, market-neutral.
4. **Replace z-score with Connors RSI(2) / RSI(3)** — different signal
   architecture; literature is dense with RSI-MR results on equities.
5. **Holding-time exit** as alternative — close after N bars regardless of z.
   Cuts the tail risk without crystallizing on z-events.
6. **5m TF on the same universe** — user wants high frequency; 5m would 3x
   the bar count, but cost-per-trade becomes structurally lethal at 5.5bps.
   Test only after a confirmed edge on 15m.

## Recommendation
Pause incremental tuning. Pick one of the structural changes above
(strong priors: #3 cross-sectional ranking or #1 return-based z) — they
test a different inefficiency, not refinements of the same one.
