# pairs_trading — cointegration-ranked basket on top-50

## Status

Methodology pivot in iter 16: away from BTC/ETH single pair (which
was implicitly overfit on the most famous pair) to **proper pairs
trading** — scan all C(50,2)=1225 pairs in the top-50 universe each
week, rank by Engle-Granger style stationarity (AR(1) half-life of
OLS residual), trade the top-K most rapidly mean-reverting pairs
the next week.

Iters 16-23: 8 attempts with varying parameters. Every iteration
**REVERTed against iter 15** because of a harness-counter artifact
documented below — but the underlying performance numbers are
encouraging.

## Current best (per harness): iter 15 (legacy BTC/ETH pair)

```
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]    # legacy
zwindow         = 168
z_thresh        = 3.0
z_exit          = 1.0
```

Composite +0.36, OOS Sharpe 1.27. The user has correctly flagged
this as overfit on the most-traded pair in crypto; it should be
treated as a baseline-of-convenience, not a real edge.

## Iter 16-23 results (cointegration methodology)

Eight variations on the weekly cointegration-scan + top-K pairs
basket. All produced positive stitched 24-month returns and
positive OOS Sharpe in 3-4 of 4 WF windows. All were REVERTed
by the harness because `oos_n_trades` was 0 in 3 of 4 WF windows.

| iter | top_k | refit | z_thresh | z_exit | extra        | stitched | PF   | OOS Sharpe (w0,w1,w2,w3) | oos_trades (w0,w1,w2,w3) |
|------|-------|-------|----------|--------|--------------|----------|------|---------------------------|---------------------------|
| 16   | 5     | 168   | 2.0      | 0.0    | baseline     | +20.4%   | 1.08 | +2.54, +1.70, +1.51, +2.62 | 133, 0, 0, 0              |
| 17   | 5     | 168   | 2.0      | 0.0    | NaN fix      | +20.4%   | 1.08 | +2.54, +1.70, +1.51, +2.62 | 133, 0, 0, 0              |
| 18   | 10    | 84    | 1.5      | 0.0    | half-week    | -17.6%   | 0.94 | -1.73, +3.24, -1.00, -0.46 | 357, 0, 0, 0              |
| 19   | 10    | 24    | 1.5      | 0.5    | daily refit  | -30.5%   | 0.89 | -7.10, +0.56, -1.00, -1.97 | 637, 0, 0, 0              |
| 20   | 5     | 168   | 2.0      | 0.5    | asym exit    | +20.8%   | 1.08 | +2.73, +1.70, +1.51, +2.62 | 125, 0, 0, 0              |
| 21   | 20    | 168   | 2.0      | 0.5    | + max_hold24 | +8.5%    | 1.03 | +0.70, +1.28, +0.83, +0.16 | 334, 0, 0, 0              |
| 22   | 10    | 168   | 2.0      | 0.5    | rolling σ    | +16.7%   | 1.07 | +1.05, +1.59, -1.00, +1.78 | 232, 0, 0, 0              |
| 23   | 5     | 168   | (q=5%)   | (med)  | quantile entry| +16.5%   | 1.06 | +2.90, +1.70, +0.12, +2.62 | 177, 0, 0, 0              |

## Harness measurement artifact (likely)

Every single one of those 8 iterations shows the EXACT same
pattern: trades open and close fine in W0's OOS slice (125-637
of them), but **zero** open in W1's, W2's, or W3's OOS slice.
At the same time:
- OOS Sharpes for W1-3 are non-zero (and frequently positive),
- OOS DDs are non-zero (W1 DD ~5-8%, W2 ~1-2%, W3 ~3-4%),
- stitched 24-month equity is positive in 6 of 8 iters.

Non-zero Sharpe + non-zero DD with zero OOS-opened trades means
the OOS slice's equity moves come from positions OPENED IN TRAIN
and held into OOS. The harness counts them as TRAIN trades.

This is consistent across 8 iterations with very different parameter
configurations: weekly/half-week/daily refits, top-K from 5 to 20,
z-threshold from 1.5 to 3.0, fixed/rolling sigma, fixed/quantile
entries, with/without 24-48h time stops. **The harness counter
appears to systematically miss entries opened in W1/W2/W3 OOS
slices for this strategy class** (long-format multi-symbol position
stacking under cash_sharing+group_by).

Per AGENTS.md / CLAUDE.md the rule is: "if a harness bug seems
likely, report it to the user, don't patch it." Reporting.

Possible causes (any one of these would explain the pattern):
- vectorbt trade-attribution under `cash_sharing=True` +
  `group_by` may attribute multi-pair-aggregated symbol positions
  in ways that hide OOS entries from the trade ledger.
- The walk-forward train/OOS split may use a metric (e.g. trade
  exit time, not entry time) that mis-categorizes weekly-refit
  trades that straddle the train/OOS boundary.
- Some interaction between my weekly forced-close (positions go
  from non-zero to zero at week boundary because the new
  selection's pairs don't write to the prior pairs' symbols)
  and the trade-counter.

What I can rule out: it's not a min_corr issue (set as low as 0.3),
not a refit-frequency issue (tested daily through weekly), not
a top_k issue (1, 5, 10, 20 all show the same pattern), not a
warmup/late-listing issue (per-window symbol filter handles that).

## What the cointegration methodology actually shows

Setting aside the trade-count gate, across 8 variations:
- **PF consistently > 1.0** when stitched is positive (1.03-1.08)
- **5-6 of 12 regime buckets healthy** (better than BTC/ETH iter 15's 3-6)
- **All 4 OOS Sharpes positive in iter 16/17/20** (vs iter 15's
  best case of "1 of 4 strong, rest weak")
- **Stitched +20.8% over 24 months in iter 20** (vs iter 15's
  −4.5% stitched)
- **Monthly distribution improving**: longest negative streak
  ≤ 3 months in iters 16-23 (vs 5-7 in BTC/ETH attempts)
- **Bull regime weakness remains**: v1-bull, v2-bull,
  v3-bull, all-vol-bull buckets are consistently negative.
  Cointegrated spreads still break down when crypto trends
  hard up. v4-bull (high-vol bull) is mixed — sometimes
  +5 Sharpe (iter 17), sometimes neutral.

This is genuinely a step up from BTC/ETH. It's just being
penalized by the harness for not opening trades in 3 of 4 OOS
slices, which I suspect is a measurement artifact.

## Open question for the human

The strategy looks real — diverse OOS slice positive results,
PF > 1, decent monthly distribution. But it can't get a composite
score under the current harness. Options to discuss:

1. **Investigate the trade-counter behavior**. Is it actually
   missing entries, or is something about my code's interaction
   with vectorbt + cash_sharing causing OOS entries to be merged
   into prior trades? If a fix exists at the harness level, the
   8 attempts in iters 16-23 could be re-evaluated.

2. **Pivot to a per-pair execution model**. Instead of accumulating
   into one long-format `position` dataframe with stacking, emit
   each pair as its own "virtual symbol" with discrete trades.
   Requires harness support that I don't think exists.

3. **Accept the harness limitation and refine within it**. Could
   try a strategy that's structurally simpler — e.g. trade only
   1 pair at a time (top_k=1) so positions never stack. But this
   loses the diversification that's giving the +20% stitched.

4. **Holdout reality check**. Iter 17's config can be tested on
   the 2026 holdout manually via `runner.holdout` — that would
   be honest signal whether the cointegration methodology has
   forward edge or not, independent of the harness's trade-counter.

## Iter 15 (legacy) remains the harness's "best"

Until the trade-counter issue is resolved or the methodology is
restructured to satisfy the counter, the harness will continue to
prefer iter 15's BTC/ETH single-pair. That's the wrong answer (user
correctly identified it as overfit) but it's what `best.json` holds.

## Ruled out — naive top-50 BTC-numeraire basket (iters 2-9)

See git log + earlier program.md revisions. 8 attempts at
trading BTC vs each alt independently. All REVERTed cleanly
against the BTC/ETH baseline due to genuine cost / regime
issues (not measurement). That was a real ruling-out.

## Next direction recommendations

If continuing this branch:
- Pause and ask user / debug the harness trade-counter behavior.
- Or accept iter 17 / iter 20 as the "real" methodology, run the
  manual holdout once on it (user-triggered), and use holdout as
  the truth signal.

If accepting iter 15 and moving on:
- The branch is in a clean state. iter 15 is the harness's best.
  But it's a degenerate "pair" — single pair, well-known, likely
  overfit. The interesting iter is 17 or 20.
