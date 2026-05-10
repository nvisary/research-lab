# pairs_trading — BTC/ETH log-spread mean reversion

## Baseline

On 1h bars, compute spread = log(BTC) - log(ETH). Rolling 168h
(1-week) z-score of that spread. Enter when |z| > 2; exit when z
returns to 0. Symmetric long/short of the spread.

```
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DEFAULT_TF      = "1h"
RAW_SIZING      = True
zwindow         = 168
z_thresh        = 2.0
z_exit          = 0.0
leg_size        = 0.5     # 50% per leg → 100% gross, ~0% net
```

Position semantics (RAW mode):
- spread state +1 → BTC = +0.5, ETH = -0.5  (long BTC / short ETH)
- spread state -1 → BTC = -0.5, ETH = +0.5  (short BTC / long ETH)
- spread state  0 → flat both legs

## Hypothesis

BTC and ETH share a dominant common factor (broad crypto beta). Their
log-spread is empirically stationary on multi-day horizons. Large
z-score deviations (>2σ) are mostly noise / liquidity events that
mean-revert within the holding window. By trading the spread rather
than direction, we hedge out the dominant beta and isolate idiosyncratic
mispricing.

## Why this slot in the strategy zoo

Statistical arbitrage / market-neutral. Orthogonal to:
- mom_tsmom / mom_xsection — directional, beta-loaded.
- mr_zscore — per-asset MR, NOT cross-asset relative.
- mr_xsection — cross-sectional rank within a basket; doesn't trade
  a specific pair's stationary spread.

Pairs trading is the only entry that actively shorts one asset against
another, so its return stream should have low correlation with the
rest of the lineup.

## Known caveats up front

- **Funding is asymmetric across legs.** Each leg pays/receives funding
  every 8h. With BTC and ETH funding both typically slightly positive,
  the short leg earns funding while the long leg pays — on average
  roughly cancels, but in funding-spike regimes the spread can be
  swamped by the funding differential. The harness subtracts both
  legs' funding from equity, so this is honestly accounted for.
- **Single pair = small sample.** Only one spread to trade. Trade
  count will be lower than basket strategies; expect to fight the
  `< 50 trades` penalty.
- **Hedge ratio is fixed at 1:1 in log-space.** A proper Engle-Granger
  / OLS rolling-β might be needed; left as the first improvement
  hypothesis after we see how naive log-spread does.
- **No cointegration test.** We assume the spread is stationary; if
  BTC/ETH ratio structurally drifts (e.g. ETH narrative cycles), the
  z-score becomes biased. A rolling ADF test or Engle-Granger residual
  could gate entries.

## Open questions / next hypotheses

- Rolling OLS hedge ratio β instead of fixed 1:1: log(BTC) - β · log(ETH).
- z_thresh sweep — 1.5 / 2.0 / 2.5 / 3.0.
- zwindow sweep — 24 (1d) / 72 (3d) / 168 (1w) / 336 (2w) / 720 (1m).
- z_exit asymmetry — tighter exit on funding-disadvantaged side?
- Half-life-based time stop: if spread hasn't reverted within N×half_life, exit.
- Cointegration gate: only trade when rolling ADF p < 0.1.
- Pair selection: try BTC/SOL, ETH/SOL, BTC/BNB; pick the most stationary.
- Multi-pair basket: independent state machines on top correlated pairs.

## Iter log

(populated by runner.iterate)
