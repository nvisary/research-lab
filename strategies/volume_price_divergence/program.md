# Volume Price Divergence

Hypothesis: a one-hour buying/selling climax is a short-term exhaustion signal
when abnormal volume and a large trend-side wick fail to produce continuation on
the next bar. The baseline fades that failed push and exits at either the wick
extreme stop or a 50% retracement of the prior impulse.

Baseline:
- Universe: BTCUSDT, ETHUSDT, SOLUSDT, AVAXUSDT on 1h bars.
- Climax volume: current volume above the prior 30-day 95th percentile.
- Trend-side wick: upper wick for up-climax shorts, lower wick for down-climax
  longs, at least 50% of the candle range.
- Prior impulse: 24h move of at least 2.5%.
- Confirmation: the next bar has at least 70% of climax volume but cannot make
  meaningful progress beyond the climax extreme.
- Entry: fade the trend after confirmation. Because the confirmation candle must
  close first, positions are shifted one bar to stay lookahead-safe.
- Exit: stop beyond the climax wick, target at a 50% retracement of the prior
  impulse, or max hold.

Iteration log:
- Baseline created as a direct implementation of the buying/selling climax
  reversal idea. The main uncertainty is whether the confirmation requirement
  leaves enough trades after fees and the time-in-position penalty.
- Iter 2: lower volume threshold from monthly top 5% to top 10%. Hypothesis:
  the initial climax filter is too sparse, including 0 trades in the last OOS
  window; a slightly broader anomaly definition may create enough samples
  without changing the wick/confirmation thesis.
- Iter 3: disable shorts. Ledger from Iter 2 showed longs carried the stitched
  edge while OOS shorts created the worst losses, especially late-2024 and
  late-2025 squeeze reversals.
- Iter 4: lower volume threshold from top 10% to top 15% after the long-only
  branch improved trade shape but stayed too inactive for the WF score.
- Iter 5: test one more activity step, top 15% to top 20% volume, to see
  whether the long-only reversal edge has a broader plateau or starts admitting
  ordinary pullbacks.
- Iter 16-25: ran a 10-step improvement block from the iter 10 champion. Removing
  AVAX, changing max hold, and changing the confirmation progress buffer did not
  beat the champion. Lowering the wick threshold helped activity but failed the
  keep margin, with 35% the best rejected neighbor. The accepted improvement was
  lowering the volume threshold from the 85th to the 80th percentile after the
  3.5% selloff filter was already in place. A further step to the 75th percentile
  added trades but reduced PF/expectancy and was reverted.
