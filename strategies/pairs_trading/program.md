# pairs_trading — BTC/ETH log-spread mean reversion

## Current best (iter 15)

```
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DEFAULT_TF      = "1h"
RAW_SIZING      = True
zwindow         = 168     # 1-week rolling z-score
z_thresh        = 3.0     # |z| > 3 to enter (extreme deviations only)
z_exit          = 1.0     # exit at |z| <= 1.0 (early profit lock)
leg_size        = 0.5
```

Score: composite **+0.36**, OOS Sharpe **+1.27**, MaxDD 6.3%, 4 OOS
trades (low_trades penalty active), DSR 0.26, PF 0.97, 6/12 regime
buckets healthy. **Caveats:** stitched 24-month return is −4.5%
despite +1.27 Sharpe — the WF aggregator surfaces good slices that
the stitched book bleeds back; one fat trade is 399% of net PnL.
This is a marginal edge dressed up by sample.

## Hypothesis history (iters 1–15)

| iter | verdict   | composite | hypothesis                                                                                  |
|------|-----------|-----------|---------------------------------------------------------------------------------------------|
| 1    | BASELINE  | -0.37     | BTC/ETH log-spread, zwindow=168, z=2, exit z=0                                              |
| 2    | REVERT    | -2.93     | top-50 universe, BTC numeraire, naive log-spread, 49 pairs                                  |
| 3    | REVERT    | -1.66     | top-50 + rolling-OLS hedge ratio β                                                          |
| 4    | REVERT    | -2.74     | top-50 + OLS β + rolling correlation gate (corr >= 0.6)                                     |
| 5    | REVERT    | -3.40     | top-50 + OLS β + BTC trend gate (only trade when BTC flat)                                  |
| 6    | REVERT    | -0.84     | top-50 + OLS β + z_thresh=3.0 + max_hold=24                                                 |
| 7    | REVERT    | -1.94     | top-50 + OLS β + max_hold=24 + faster zwindow=72                                            |
| 8    | REVERT    | -2.27     | top-50 + dynamic top-N=10 by rolling corr (entry gate)                                      |
| 9    | REVERT    | -2.17     | top-50 + top-N=10 + long-only spread (no short-spread entries)                              |
| 10   | REVERT    | -1.27     | back to BTC/ETH baseline + max_hold=24 (time stop hurt — spread reverts slowly)             |
| 11   | KEEP      | -0.12     | BTC/ETH + z_thresh 2 → 2.5 (tighter entry); first improvement                               |
| 12   | KEEP      | +0.07     | + z_exit 0.0 → 0.5 (asymmetric early exit); first positive composite                        |
| 13   | KEEP      | +0.22     | + z_thresh 2.5 → 3.0 (only extreme deviations)                                              |
| 14   | REVERT    | -0.45     | zwindow 168 → 336 (2-week); train/oos gap blew up, overfit                                  |
| 15   | KEEP      | **+0.36** | + z_exit 0.5 → 1.0 (lock profit even earlier); current best                                 |

## Ruled out: top-50 BTC-numeraire pairs basket (iters 2–9)

Eight diverse hypotheses at top-50 universe scope all failed
(every one REVERTed against BTC/ETH baseline; best of the eight
was -0.84 composite vs baseline -0.37):

- naive log-spread (no hedge ratio)
- rolling-OLS β hedge ratio
- rolling-correlation entry gate (corr >= 0.6)
- BTC realized-trend gate (only trade in flat regime)
- z_thresh=3.0 + 24h time stop
- faster zwindow=72
- dynamic top-10 by rolling correlation rank
- long-spread-only (drop short-spread entries)

Pattern across all eight: bull-trend regimes (especially v3-bull)
ate the basket. In crypto bull runs, alts pump faster than BTC and
log-spreads extend persistently against the mean-reversion thesis.
β-scaling, trend gates, and corr filters mitigated but never
reversed the regime-conditional drag. Trade counts ranged 540–10,000
with PF stuck in [0.76, 0.95] — too many shallow signals where
costs (5.5 bps × leg × pair) compounded faster than the spread
mean reverted.

**Open question for the human:** is this fundamental to the
universe (alts not co-integrated with BTC) or to the cost
structure? Possible follow-ups outside the iter budget:
- pre-select 5–10 pairs by long-window cointegration / Engle-Granger
  (strict, hardcoded; effectively a curated universe)
- alt/alt pairs (ETH/SOL, ETH/BNB) instead of *_/BTC
- much higher TF (4h, 1d) to amortise costs over fewer, larger trades
- Ornstein-Uhlenbeck half-life filter (only trade pairs with
  measured half-life < 12 bars)

## What worked on the BTC/ETH single pair (iters 11–15)

The improvement axis was **selectivity of signals**, not the signal
shape itself:
- Tighter entry (z=3 vs z=2): fewer trades, larger expected reversion.
- Earlier exit (z_exit=1.0 vs 0.0): lock partial reversion before
  funding drag eats the rest. BTC perp funding accumulates ~1bp/8h
  on average, so holding through full reversion is taxed.
- Stable zwindow at 168 (2-week was overfit, faster windows spec'd
  badly on the 24mo train).

These together pushed composite −0.37 → +0.36 and brought 6/12
regime buckets to healthy.

## Caveats / what's NOT yet established

- **Tiny trade count** — 4 OOS trades in current best. Penalty is
  active. The +1.27 Sharpe is noisy.
- **Fat-tail dominance** — one trade is 399% of net stitched PnL,
  meaning the rest of the book is net negative.
- **WF vs stitched divergence** — Sharpe +1.27 over WF slices but
  −4.5% compounded over 24 months. The harness flags this as
  "edge lives in OOS slices only" — likely calendar bias from how
  the splits land.
- **DSR at 0.26** (was 0.77 at baseline) — selection-bias tax across
  15 iters is real. Discount the headline composite accordingly.
- **No holdout taken yet.** The 2026 holdout is the only honest
  measure of forward edge from here.

## Open questions / next direction

If continuing on the BTC/ETH track:
- Funding-aware entry: skip when funding asymmetry penalises the
  long leg of the planned trade.
- Test on 4h decision TF — fewer trades, less cost drag per round-trip.
- Add a stop-loss at z=−5 to bound the catastrophic-divergence tail.
- Test BTC/SOL, BTC/BNB single pairs as alternatives.

If revisiting the basket:
- Hardcoded curated 5–10 cointegrated pairs (drop the universe
  scope, treat as discrete strategies stacked).
- Alt/alt rather than alt/BTC.
- Higher TF (4h/1d) to amortise costs.

## Iter log notes (sparse — see history.jsonl for full)

Each KEEP iter table row above corresponds to a numbered entry in
`runs/history.jsonl`. The diagnostics JSON in each entry has the
full per-window decomposition + flags. The HTML tearsheets at
`runs/tearsheets/iter_NNNN.html` are the human-review artefacts.
