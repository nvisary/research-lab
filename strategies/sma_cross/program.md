# SMA Crossover — research strategy

## Hypothesis
A simple SMA crossover on BTC has weak edge that's swamped by chop and
funding drag. Research questions:

1. Does a higher-TF trend filter recover composite by skipping counter-regime trades?
2. Can volatility-targeted sizing tame the long-only bias-induced DD?
3. Are there parameter ranges where the crossover is genuinely robust
   across walk-forward windows?

## Current champion (iter 27)
- TF=4h, single symbol BTCUSDT
- SMA(20/100), long-only (no shorts), no cooldown
- Volatility-targeted sizing: target_daily_vol=0.02, vol_lookback=21 (3.5d), bars_per_day=6
- Composite **+1.34** | OOS Sharpe **+2.75** | OOS MaxDD 7.8% | DSR 0.78

## What's been ruled out

- **4h SMA(50) trend gate (iter 12)** and **1d SMA(50) trend gate (iter 19)**:
  external trend filters degrade the SMA cross — the cross IS the trend filter,
  layering another only kills good entries.
- **Low-vol regime filter (iter 13)**: ATR/close < q20 skipped good trades too.
- **Slower SMAs (30/150 at 4h, iter 15) and faster (15/75, iter 16)**: 20/100
  is the sweet spot at 4h; deviating in either direction worsens composite.
- **Continuous strength position (iter 20)**: replacing ±1 with `clip(gap*k, -1, 1)`
  reduced the average position size and lost edge — full conviction wins on this
  single-asset trend strategy.
- **Switching symbol to ETH (iter 21)** or **BTC+ETH basket (iter 22)**: ETH at 4h
  with these params yielded composite -4.4 / -2.3. ETH alone has different
  trend properties; basket adds noise without diversification benefit.
- **EMA fast/slow (iter 28)**: faster turn detection didn't help.
- **vol_lookback parameter sweep (iter 23/25/26)**: 21 narrowly best, neighbors
  (12, 30, 42, 84) all worse. Borderline overfit territory — treat with skepticism.
- **slow=80 (iter 29) and slow=120 (iter 30)**: both worse than slow=100.

## Key empirical findings
- **Decision TF matters more than indicator choice**: switching 1h→4h alone
  jumped composite from -0.58 to -0.21 (Δ +0.37) at iter 14.
- **Long-only is the single biggest win**: at 4h on BTC, dropping shorts
  jumped composite +0.78 (iter 27). BTC's structural up-trend + funding makes
  short legs cost more than they earn on a trend follower.
- **Cooldown belongs to fast TFs**: cooldown=6 helped at 1h (iter 8) but hurt at 4h
  (iter 17 dropped it to 0 → composite +0.57 jump). At 4h whipsaws are inherently
  rare.

## Caveats
- DSR is 0.78 (< 0.95). After 30 iterations on this strategy, selection bias
  is non-trivial. Treat the iter-27 metrics with skepticism until holdout speaks.
- WF window 3 (later 2025) had only 1 OOS trade for the champion — the strategy
  is genuinely "off" in a chop window. Composite still positive only because
  windows 0-2 carry a 6+ Sharpe.
