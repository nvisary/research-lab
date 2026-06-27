# pump_dump_combined — pump + dump portfolio research

Separate research thread (sibling to `../pump/` and `../dump/`). Same playbook
(`../pump/HOW_WE_WORK.md`): one honest step at a time, baseline, pooling, mind
costs, flag lookahead. Helper/data shared (`_lab.py`).

## The question
Pump-fade (SHORT after +5%/15m) and dump-bounce (LONG after −7%/15m) are
mechanically OPPOSITE mean-reversion streams. Hypothesis (user): they are
uncorrelated — possibly hedge each other's worst regimes (pump-shorts suffer in
a melt-up, dump-longs suffer in a crash; those are opposite regimes) — so combining
them yields a smoother curve and lower drawdown than either alone.

Both streams built with the SAME machinery as the final dump strategy (scale-in,
price-step fill, exit cluster_end+240, net fees), just mirrored in sign, on the
full 173-symbol universe 2024-2026. Core configs (no classifier/tail-limits) for a
clean correlation read; those improvements are orthogonal and can be layered later.

## Notebooks
- `00_pump_plus_dump` — both streams (full universe, scale-in price-step), daily-
  return correlation, combined 50/50 sleeves, regime-hedge check. **Result: thesis
  confirmed — daily corr = −0.35, combined 50/50 Sharpe 4.12 (vs 1.99 dump / 2.72
  pump alone), maxDD −3.6% (< both). Regime hedge literal: dump's worst days → pump
  positive & vice versa; both negative only 6.3% of days.** HONEST FLAG: the
  simplified pump mirror has marginal per-trade edge (+0.02%/trade w/o volume filter
  + classifier); its portfolio +165% is largely a capacity-clipping artifact (skips
  correlated melt-up losers) — pump's absolute level is NOT trustworthy yet. Robust
  takeaway = the negative correlation + diversification, not pump's absolute number.

- `01_faithful_combined` — **pump rebuilt faithfully (vol-gate +5%/15m AND vol>3×
  + walk-forward classifier) + tail-limits on both + reinvest + DCA.** Pump: vol-gate
  alone doesn't fix raw edge (−0.08%/trade); the CLASSIFIER does (pred>0+stop+KS →
  +0.69%/trade, win 61%; but OOS corr only +0.079 = thin/fragile). Dump after layers
  +2.57%. **Daily corr −0.50.** Reinvest 2%, $1000: dump $2550/CAGR50%/Sh1.97;
  pump $3818/78%/3.75; **COMBINED $11504/CAGR186%/Sharpe5.04/DD−9.5%.** DCA $100/mo
  ×28=$2800 → robot $12205 (4.36× cash). **HONEST: diversification is robust (corr
  −0.50, combined Sharpe≫each, DD lower); but the ABSOLUTE (186% CAGR, 4.36× DCA) is
  OPTIMISTIC — 2%-compound × ~10.8k trades is explosive, assumes infinite capacity/
  divisibility (microcaps cap it), pump rests on a thin classifier, Sharpe 5 is
  unrealistic live.** Read magnitude as a ceiling, not a forecast.

- `02_realistic_capacity` — **capacity cap + market-impact slippage → believable
  absolute + the SCALING WALL.** Median coin liquidity ~$2.7–3.1k/min (thin
  microcaps). Same combined stream, reinvest 2%, by START capital: $1k→CAGR 140%/
  Sharpe 4.34; $10k→99%/3.53; $50k→64%/2.74; $200k→36%/2.04; **$1M→11.6%/Sharpe 0.99
  (69% of trades capacity-capped, $744k slippage).** Strategy does NOT scale — a
  small-capital niche (exactly why such edge isn't arbitraged by large money).
  **Realistic DCA $100/mo: $2,800 in → $9,064 by 2026 (3.24×), vs optimistic $12,205**
  — account stays small so capacity barely binds (4% capped). Robust: scaling wall +
  DCA shape. Caveats: slippage params estimated, latency/fills unmodeled, thin pump
  classifier, live=0 → $1k/140% is an in-sample ceiling.

## State / next
Diversification CONFIRMED (corr −0.50). Pump faithful. **Believable absolute now
bounded by the scaling wall: it's a small-capital strategy (CAGR collapses 140%→12%
from $1k→$1M); realistic $100/mo DCA ≈ $9k by 2026 (in-sample ceiling).** Next:
anti-overfit of thin pump classifier; slippage-param sensitivity; graduation to
strategies/ + live paper bot (small size, default-on tail limits).
