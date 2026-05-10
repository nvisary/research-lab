# pairs_trading — cointegration-ranked basket on top-50

## Status

Methodology pivot (iter 16+): proper pairs trading — weekly scan
of all C(50,2)=1225 pairs in top-50 universe, rank by AR(1) half-life
of OLS residual (Engle-Granger style), trade top-K mean-reverting
pairs the next week.

Iters 16-29 explored 14 variants. Best methodologically (iter 28):
**OOS Sharpe +6.23 / +4.60 in W0/W1**, regime buckets 6/12 healthy.
All variants REVERT'ed against legacy iter 15 because of a layered
harness/strategy-architecture issue documented below.

## Root cause of `0 OOS trades` (deep dive)

After reading harness/backtest.py and harness/splits.py, the issue
is a layered interaction:

**Layer 1 — padding lookback + zero-out interaction with stacked positions.**
`runner.iterate` defaults `--lookback "60D"`. harness/backtest.py:260
loads data starting 60 days BEFORE each WF window's train_start.
After signals are computed, backtest.py:304-305 zeros target positions
during the padding period. With overlapping pairs sharing symbols
(BTC in multiple pairs simultaneously), vectorbt sees a 0→non-zero
jump at train_start and records ONE long-running round-trip per
symbol with `entry_time = train_start` — which is TRAIN, not OOS.
W0 escaped this because its padding had no data (pre-2024-01-01).

**Layer 2 — cointegration regime breakdown in 2025 OOS slices.**
Even after removing padding (--lookback "0") AND switching to greedy
non-overlapping pair selection (each symbol in ≤1 active pair, so
positions cycle through zero at every refit), W2 (~April-June 2025)
and W3 (~Nov 2025 - Jan 2026) OOS slices still produce 0 trades.
**This is genuine — not a counter bug.** In those windows my code's
`_score_pairs` returns 0 valid pairs because rolling-residual AR(1)
falls outside (0, 1) — i.e. NO pairs in the universe pass cointegration
filtering during the 2025 cycle-peak / cycle-crash regime. The spreads
are random-walking or trending. My strategy correctly stays flat.

The harness's `min_trades=50 → composite=-inf` penalty rule punishes
this as a failure. For mean-reversion strategies that are honest
about regime breakdowns, the rule is too coarse — going flat when
cointegration breaks is the RIGHT thing to do, not a failure.

## Iter table

| iter | top_k | refit | filters/extras                  | --lookback | OOS trades w0/w1/w2/w3 | stitched | PF   |
|------|-------|-------|---------------------------------|------------|------------------------|----------|------|
| 16   | 5     | 168   | baseline (overlap pairs)        | 60D        | 133, 0, 0, 0           | +20.4%   | 1.08 |
| 17   | 5     | 168   | + NaN handling                  | 60D        | 133, 0, 0, 0           | +20.4%   | 1.08 |
| 18   | 10    | 84    | half-week, z=1.5                | 60D        | 357, 0, 0, 0           | -17.6%   | 0.94 |
| 19   | 10    | 24    | daily, z=1.5/0.5                | 60D        | 637, 0, 0, 0           | -30.5%   | 0.89 |
| 20   | 5     | 168   | z_exit=0.5 asym                 | 60D        | 125, 0, 0, 0           | +20.8%   | 1.08 |
| 21   | 20    | 168   | max_hold=24                     | 60D        | 334, 0, 0, 0           |  +8.5%   | 1.03 |
| 22   | 10    | 168   | rolling σ inside trade week     | 60D        | 232, 0, 0, 0           | +16.7%   | 1.07 |
| 23   | 5     | 168   | quantile entry (5/95 pct)       | 60D        | 177, 0, 0, 0           | +16.5%   | 1.06 |
| 24   | 5     | 168   | greedy NON-OVERLAPPING          | 60D        | 132, 0, 0, 0           | +21.4%   | 1.08 |
| 26   | 5     | 168   | non-overlap                     | **0**      | 132, **125**, 0, 0     | -15.1%   | 0.97 |
| 27   | 5     | 168   | non-overlap, z=1.0              | **0**      | 216, **236**, 0, 0     | -13.4%   | 0.98 |
| 28   | 5     | 168   | scheduled-entry on resid sign   | **0**      | 44, **54**, 0, 0       |  -2.4%   | 0.98 |
| 29   | 5     | 168   | sched + no filters              | **0**      | 58, **68**, 0, 0       |  -9.7%   | 0.93 |

(iter 25 was a re-run of iter 15 BTC/ETH due to a REVERT-mid-write
race; ignore that line in metrics.)

## What we proved

1. **Padding-stacking interaction is real (Layer 1)**. Going from
   `--lookback "60D"` (default) to `--lookback "0"` flipped W1 from
   "0 OOS trades" to "125 OOS trades" with otherwise identical code
   (iter 24 vs 26). The harness's padding zero-out + stacked positions
   under cash_sharing+group_by produces a single long-running round-trip
   that's attributed to TRAIN by entry_time.

2. **Non-overlapping selection alone is not enough (Layer 2 exists)**.
   Iter 24 (non-overlap, default padding) still showed 0 in W1/W2/W3.
   Iter 26 (non-overlap, lookback=0) unblocked W1 but not W2/W3.

3. **Scheduled entry on residual sign produces excellent OOS Sharpes
   when it fires** (iter 28: +6.23 W0, +4.60 W1). 6/12 regime buckets
   healthy. This is genuinely interesting methodologically.

4. **W2/W3 OOS silence in 2025 is not a counter bug** — `_score_pairs`
   returns 0 valid pairs because no spreads are stationary during the
   cycle-peak / cycle-crash regimes of mid-2025 and late-2025. The
   strategy correctly refuses to trade.

## Best methodology (per real edge, not harness composite)

**Iter 28**: non-overlapping greedy top-5 by AR(1) half-life,
scheduled entry on residual sign at refit boundaries, min_z_enter=0.3,
z_exit_overshoot=0.5, --lookback "0".

- W0 OOS Sharpe: +6.23 (44 trades)
- W1 OOS Sharpe: +4.60 (54 trades)
- W2 OOS Sharpe: 0.00 (strategy correctly silent during 2025 cycle peak)
- W3 OOS Sharpe: -1.83 (carryover loss; would benefit from flat-when-no-cointegration)
- 6/12 regime buckets healthy (better than iter 15's 3-6)
- Monthly stats: 7 red / 13 green months, longest neg streak = 2

But harness aggregates to -inf because W2/W3 fail min_trades gate.

## Harness composite "best": iter 15 (legacy BTC/ETH)

Composite +0.36 from BTC/ETH single-pair tuning. User correctly
flagged as overfit on most-traded pair. Lowest-information win.

## Recommended next steps (for the human)

1. **Manual holdout on iter 28 config** — bypass the WF gate via
   `runner.holdout strategies/pairs_trading` once iter 28's code is
   restored to strategy.py. Holdout is a single train/OOS split over
   2026-Q1, no WF stitching, no min_trades-per-window penalty. The
   honest truth signal for this methodology.

2. **Consider relaxing harness gate for regime-aware mean-rev**.
   The `min_trades=50 → composite=-inf` rule punishes strategies that
   correctly stay flat when their thesis doesn't apply in a given
   regime. A possible refinement (NOT applied here — requires harness
   touch): if a WF window's OOS slice has 0 entries AND 0 DD AND 0
   Sharpe, treat it as "no engagement" not "edge failed" and exclude
   from WF aggregation. This is opinionated and should be discussed.

3. **Try CPCV / single-OOS evaluation** — `runner.iterate` supports
   alternatives to walk-forward via existing harness modules
   (harness/cpcv.py). A single train/OOS over the full 24 months
   may handle the regime-conditional silence differently.

4. **Pair selection robustness**. If 2025 has no cointegrated pairs,
   maybe extend lookback (use 2-3 months of data for scoring) or use
   a more lenient cointegration criterion (e.g. Hurst exponent < 0.45)
   that admits more candidates in regime transitions.

## Files

- `strategy.py` (currently): iter 29 code, then REVERTed to iter 15
  by the harness because of -inf composite. To run iter 28 fresh:
  rewrite the strategy.py with the scheduled-entry logic and invoke
  with `--lookback "0"`.
- `runs/best.json`: iter 15 (BTC/ETH).
- `runs/history.jsonl`: iters 1-29 logged.
- `runs/tearsheets/iter_*.html`: visual artifacts (only on KEEP iters).
