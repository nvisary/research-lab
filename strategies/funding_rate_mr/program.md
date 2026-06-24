# funding_rate_mr

## Thesis
Extreme 8h perp funding is a direct crowding signal. When funding reaches
about +/-0.10% and price is simultaneously stretched outside a 4h Bollinger
band, the consensus trade should be expensive enough to fade. The baseline
shorts high positive funding above the upper band and buys high negative
funding below the lower band.

## Baseline logic
- Universe: available Bybit USDT-perp alt symbols with both OHLCV and funding
  parquet on disk, excluding BTC and ETH. This is 30 symbols locally, not a
  true delisting-aware top-50 universe.
- Decision TF: 4h.
- Entry: funding >= +0.10% and close > upper 20-period 2-sigma Bollinger band
  for shorts; funding <= -0.10% and close < lower band for longs.
- Sizing: raw equal-risk slot, `1 / n_symbols` per active symbol, so the basket
  remains inside the harness 100% cash-sharing cap.
- Exit: funding normalizes to +/-0.01%, a fixed ATR bracket resolves with
  R:R = 1:2.5, or the trade times out after 3 days.

## First hypothesis note
Baseline funding-crowding fade: extreme +/-0.10% funding plus 4h Bollinger
extension should identify overheated alt-perp consensus trades; expect sparse
but economically meaningful reversals after funding normalizes.

## Iteration notes
- Iter 1 baseline MR with +/-0.10% funding + 4h BB was too sparse: 0 OOS
  trades in two windows, composite forced to -inf.
- Iter 2 lowering the funding entry to +/-0.05% increased trades but made all
  OOS windows negative; this ruled out simple threshold loosening.
- Iter 3 adding an EMA regime gate almost eliminated trading; too restrictive.
- Iter 4 removing the BB requirement produced enough trades to beat the
  -inf baseline but remained negative OOS and PF < 1.
- Iter 5 disabling the funding-normalization exit helped slightly but remained
  a negative MR variant.
- Iter 6 flipped direction as a falsification check. Funding-extreme
  continuation/carry became current best, but it is not robust yet: OOS Sharpe
  is positive with only ~10 OOS trades, stitched 24-month return is negative,
  and diagnostics flag fat-tail dependence.
- Iter 7 shortened max hold from 3 days to 1 day. This fixed the worst tail
  shape enough to produce the first positive composite and positive stitched
  return.
- Iter 8 lowered the continuation threshold from +/-0.10% to +/-0.05% after
  the shorter hold. This is the current champion: enough OOS trades and market
  time to clear the activity penalties, stitched return positive, fat-tail
  dependence much lower.
- Iter 9 tested EMA trend alignment. It improved stitched return but failed the
  walk-forward composite, so the harness reverted it.
- Iter 10 and 11 tightened ATR stops to 1.0 and 0.8. The 1.0 stop was close but
  did not clear the +0.01 keep threshold; 0.8 was too tight.
- Iter 12 lowered reward/risk to 1.5. Earlier profit-taking reduced edge and
  increased drawdown, so keeping the 2.5 target is better.
- Iter 13 tested long-only positive-funding carry. It removed needed short-side
  regime coverage and produced a zero-trade OOS window.
- Iter 14 added a 6-bar cooldown. It reduced churn but also reduced expectancy
  and stitched return; repeated entries are not the main defect.
- Iter 15 tested max hold 8 bars. Composite improved by only +0.0099, just
  below the +0.01 keep threshold, with worse drawdown than the 6-bar champion.
- Iter 16 tested max hold 7 bars. It also failed to beat the 6-bar champion;
  the one-day hold remains the best risk/impulse balance.
- Iter 17 raised the continuation threshold to +/-0.075%. This is the new
  champion: much stronger OOS Sharpe, lower drawdown, higher PF, and positive
  stitched return, but OOS trade count is below 50 and DSR remains weak.
- Iter 18 lowered the threshold to +/-0.065%. It produced all four OOS windows
  positive and nearly passed, but the composite lift was +0.0097, below the
  keep threshold.
- Iter 19 tested the midpoint +/-0.070%. It was worse than both iter 17 and
  iter 18; keep the stricter +/-0.075% crowding gate for now.
- Iter 20 tested an adaptive per-symbol funding threshold: rolling 90th
  percentile of absolute funding with a +/-0.05% floor. It solved the
  "quiet 2025 months" problem operationally (more trades, all OOS windows
  positive, stitched +23%), but drawdown and W3 dominance made composite worse
  than the fixed +/-0.075% champion.
- Iter 21 repeated the adaptive threshold with a stricter +/-0.065% floor. It
  also produced all OOS windows positive and better stitched/PF shape, but did
  not beat iter 17's composite. For the harness objective, fixed +/-0.075%
  remains the champion; for a human preference toward smoother activity,
  adaptive floor 0.065 is a plausible branch to revisit.
- Iter 22 added a two-level gate: fixed +/-0.075% entries plus +/-0.05%
  fallback only when 24h return agrees and volume is above its local median.
  This became a new champion by increasing OOS trades while keeping all windows
  positive.
- Iter 23 added a persistence fallback: moderate +/-0.035% funding must persist
  for six 4h bars and 24h return must agree. This became the new champion,
  lifting composite to 2.36 and improving OOS Sharpe/trades without increasing
  drawdown.
- Iter 24 tested a cross-sectional top/bottom funding-rank fallback. It added
  activity but hurt quality and made the first OOS window negative; reverted.
- Iter 25 tested asymmetric long/short thresholds. PF improved but drawdown and
  composite were worse than iter 23; reverted.
- Iter 26 added a general adaptive rolling-quantile fallback with a +/-0.065%
  floor and return confirmation. It was close but slightly worse than iter 23.
- Iter 27 tested the quiet-regime adaptive fallback specifically. It behaved
  almost like iter 26 and did not beat the persistence champion. For now the
  best solution to 2025 quiet-month inactivity is the two-level gate plus
  persistent moderate-funding fallback, not adaptive rank/quantile logic.
