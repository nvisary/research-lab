# pump_dump_m1

## Thesis

Sharp one-minute price shocks accompanied by abnormal volume sometimes overshoot
because late takers cross the spread into a temporary liquidity vacuum. The
researchable trade is not joining the move; it is fading the public-market-data
shock after a delay and testing whether short-horizon reversion survives costs.

## Baseline

- Decision timeframe: 1 minute.
- Universe: liquid Bybit USDT perps plus a few more reactive alts.
- Pump event: 1m log return z-score above threshold and volume z-score above
  threshold.
- Dump event: 1m log return z-score below negative threshold and volume z-score
  above threshold.
- Reaction: short pumps, long dumps.
- Execution assumption: enter after at least one full bar of delay, then hold a
  fixed number of bars.
- Sizing: raw total-equity sizing, small fixed fraction per active event.

## First Questions

- Is event-level median PnL positive after Bybit taker costs?
- Does the effect survive a 2-3 bar entry delay?
- Are results concentrated in one symbol or month?
- Do thin/high-beta symbols drive all apparent edge?

## Rules

- No holdout during iteration.
- Do not use external files or future data inside `generate_signals`.
- Keep final positions dependent only on completed prior bars.
- Prefer one structural change per iteration.
