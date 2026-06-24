# Volume-Weighted Breakout

Hypothesis: a 4h support/resistance breakout is more reliable when the breakout
bar shows abnormal classic candle volume. Baseline enters on a local high/low
break with breakout-bar volume at least 3x its 20-bar volume SMA, exits via a
fixed 2.5x ATR take-profit or stop at the midpoint of the breakout bar.

Iteration log:
- Baseline: BTC/ETH/SOL 4h breakout on anomalous volume, ATR bracket exit.
- Iter 1 baseline: volume >= 3x was too sparse and weak across all WF windows.
- Iter 2: 4h EMA-slope flat filter reduced activity too much and reverted.
- Iter 3 KEEP: lower volume threshold 3x -> 2x improved sample size and trade
  shape, but WF OOS stayed negative.
- Iter 4: shorten max hold 30 -> 12 bars worsened late-window OOS and reverted.
- Iter 5: widen breakout lookback 20 -> 40 reduced activity and reverted.
- Iter 6 KEEP: shorten breakout lookback 20 -> 10 improved composite and sample
  size, consistent with a fresher impulse effect.
- Iter 7: reduce take-profit 2.5x -> 1.5x ATR improved early windows but hurt
  W3 enough to revert.
- Iter 8: raise take-profit 2.5x -> 3.5x ATR increased drawdown and reverted.
- Iter 9 KEEP: move stop from breakout-bar midpoint to the opposite bar extreme;
  gave volume spikes room and improved composite to -1.5524.
- Iter 10: lower volume threshold 2x -> 1.5x increased trades but worsened DD,
  PF, funding drag, and reverted.

Current read: the best variant is materially less bad than baseline, but the
walk-forward aggregate remains negative. The idea is not validated yet; W3/OOS
late-2025 remains the main failure mode.

Second round, focused on increasing active/green months:
- Iter 11: expanded BTC/ETH/SOL to a larger liquid alt universe. Trade count
  jumped, but W2/W3 deteriorated and the branch reverted.
- Iter 12: confirmed 1.5x volume fallback with 24h momentum acted like a noisy
  global loosening and reverted.
- Iter 13/14: asymmetric long/short volume thresholds improved some stitched
  shape but both worsened WF composite and reverted.
- Iter 15: 1h entries against completed 4h levels created many trades but was
  structurally lossy across all WF windows and reverted.
- Iter 16: 3-bar follow-through entries after a strong breakout was close, but
  still below the champion and reverted.
- Iter 17: flat-regime failed-breakout fade with 1% EMA-slope threshold was
  close but slightly worse on drawdown/composite and reverted.
- Iter 18: middle volume threshold 1.75x was worse than 2.0x and reverted.
- Iter 19 KEEP: strict flat failed-breakout fade with 0.5% EMA-slope threshold
  improved composite to -1.3586 by repairing W2 without increasing worst DD.
- Iter 20: strict flat-fade plus larger universe reverted; alt breadth still
  adds noise.
- Iter 21: strict flat-fade plus follow-through reverted; the follow-through
  adds marginal activity but not enough quality.

Current best: BTC/ETH/SOL 4h continuation breakout plus a very strict flat
failed-breakout fade. It improves monthly shape modestly, but W3/late-2025 is
still the central weakness.

W3/W4 repair round:
- Ledger inspection showed the true remaining failure was WF window 4 OOS
  (Nov-Dec 2025), not window 3: shorts clustered after already-extended
  downside moves and then reversed sharply, especially around 2025-12-01.
- Iter 22 KEEP: add a 24h exhaustion guard, skipping longs after >8% 24h rise
  and shorts after >8% 24h drop. This improved composite to +0.0363, made
  17/24 stitched months green, lifted WF mean Sharpe to +0.73, and improved
  profit factor to 1.38 on the stitched trade shape.
- Iter 23: tightening exhaustion to 4% reduced drawdown but over-filtered good
  trades and reverted.
- Iter 24: middle 6% exhaustion guard also over-filtered W1/W2 and reverted.
- Iter 25: shorter max hold 30 -> 18 bars did not fix W4 and hurt early-window
  OOS, reverted.
- Iter 26: dropping SOL reduced sample and did not repair W4, reverted.
- Iter 27: BTC-only proxy for synchronized-breakout risk still failed W4 and
  became too sparse, reverted.

Current best: strict flat failed-breakout fade plus 8%/24h exhaustion guard.
The remaining weakness is still late-2025 W4, but the broad W3/W4 repair was
real: the strategy now clears positive WF composite while keeping 17 green
months in the stitched diagnostic.

Activity expansion round:
- Iter 28: volume 2.0x -> 1.8x added trades but slightly worsened W4/DD and
  missed keep by a small margin.
- Iter 29 KEEP: volume 2.0x -> 1.9x added a little activity while improving
  Sharpe/PF; new composite +0.0618.
- Iter 30: volume MA 20 -> 14 added activity and 17 green months, but reduced
  PF/Sharpe and reverted.
- Iter 31: volume MA 20 -> 30 also gave 17 green months but poor WF aggregate.
- Iter 32 KEEP: breakout lookback 10 -> 8 added fresh-level breakouts and
  improved composite to +0.0842.
- Iter 33: breakout lookback 8 -> 6 added more trades but diluted expectancy.
- Iter 34: add BNB alone was a near-miss: many more trades and 18 green months,
  but composite was slightly lower due to W0 dominance.
- Iter 35: add XRP degraded W2/W3 and reverted.
- Iter 36: add LINK alone was also a near-miss with 18 green months but lower
  composite due to drawdown/W4.
- Iter 37 KEEP: add AVAX alone gave the best breadth improvement: mean trades
  rose to 31.5 and composite jumped to +0.4333.
- Iter 38: AVAX+BNB added more trades but reduced composite.
- Iter 39: AVAX+LINK added more trades and 18 green months but reduced
  composite.
- Iter 40: loosen flat-fade slope 0.5% -> 0.75% added one trade but reduced
  composite.
- Iter 41: tighten flat-fade slope 0.5% -> 0.3% was behaviorally identical.
- Iter 42 KEEP: add 3-bar follow-through continuation at 1.2x volume after a
  strong breakout; composite improved to +0.4591 and mean trades to 32.75.
- Iter 43: follow-through volume 1.2x -> 1.1x added almost no useful activity
  and reduced composite.
- Iter 44: follow-through window 3 -> 5 was effectively unchanged.
- Iter 45: TP 2.5x -> 2.0x improved turnover/PF but hurt WF composite.
- Iter 46: TP 2.5x -> 3.0x was worse.
- Iter 47: max hold 30 -> 36 was a strong near-miss with better stitched PF and
  return, but slightly lower WF composite.

Current best: BTC/ETH/SOL/AVAX, 4h lookback 8, volume 1.9x MA20, strict flat
failed-breakout fade, 8%/24h exhaustion guard, 3-bar follow-through entries.
The best activity additions were AVAX breadth and follow-through entries; BNB,
LINK, and max-hold 36 are the best near-miss branches for a future combination
or risk-shape round.
