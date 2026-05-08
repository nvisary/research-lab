# SMA Crossover — research strategy

## Hypothesis
A simple SMA crossover on 1h BTC has weak edge that's swamped by chop and
funding drag. The research questions:

1. Does a higher-TF trend filter (200-MA, 4h regime) recover most of the
   lost composite by skipping counter-regime trades?
2. Can volatility-targeted sizing tame the long-only bias-induced DD?
3. Are there parameter ranges where the crossover is genuinely robust
   across walk-forward windows, or is it always a one-window-luck artifact
   like the EMA pilot?

## What can change
Anything in `strategy.py` body. `DEFAULT_PARAMS` may grow as new filter
hyperparameters are introduced. `DEFAULT_TF` should stay 1h unless we
explicitly pivot to a different decision frequency.

## What's been ruled out
(empty for now — populate as iterations refute hypotheses)
