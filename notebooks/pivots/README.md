# Daily pivot-points research

Manual research line (sibling of `notebooks/pump`, `dump`, `levels`, `rsi`) —
same slow, honest, teaching-oriented playbook. **Read `../pump/HOW_WE_WORK.md`
first**, then this journal, then the latest numbered notebook.

Helper: `_lab.py` (import with `sys.path.insert(0,'.'); from _lab import *` when
cwd is this folder). Adds `daily_pivots()` (standard floor-trader P/S1/R1/S2/R2
from the previous day's OHLC) and `cci()` on top of the standard loader. Plots
end with `show("name")` → `_out/name.png`.

## The question

`strategies/pivot_cci` is a mean-reversion strategy that scores composite **1.22**
in the harness on BTC/ETH (2024-01 → 2026-01, WF=4). Its thesis: after price
touches a **daily support/resistance level** (S1/R1 computed from yesterday's
OHLC), it reverts back toward the **daily pivot P**. On top of that bare claim it
stacks four filters — CCI momentum, RSI, an EMA200 trend gate, and a funding
gate — and exits at P or on CCI recovery.

That is a **five-layer cake**. The harness tells us the whole cake scores 1.22,
but not which layer carries the edge. This line takes the cake apart: isolate the
**bare pivot-reversion claim** first (baseline-controlled, pooled, costed), then
add each filter back one at a time and measure its **marginal** contribution.

## Why this is not the (closed) `levels` line

`levels` studied **swing-pivot** S/R (horizontal lines drawn at confirmed local
extremes) and found no OHLCV edge — and its central trap was **anchor-lookahead**
(a swing pivot is only knowable `k` bars late). **Daily pivot points are a
different construct:** a fixed *formula* on the prior day's completed candle,
held flat all day. They are **fully causal by construction** (yesterday is known),
so there is no anchor-selection trap here. Whether a mechanical formula level has
any more edge than a hindsight-drawn one is exactly the open question.

## Discipline we carry over

Baseline always (a touch-reversion number is meaningless without "what does price
do at a random bar?"). Pool across many symbols for sample size. Win-rate is a
trap — report mean/median/win%/std/q10. Costs: alt perp round-trip ≈ 0.1–0.2%.
No lookahead (daily pivots are causal; any added filter must be too). 2024 =
train, 2025 = sealed OOS. Honest caveats every step.

## Notebook journal

- `01_pivots_intro` — INTRO / vocabulary. What daily pivot points are (the
  floor-trader formula + intuition: yesterday's fair value and its reflections),
  drawn on BTCUSDT over a handful of days so the daily step-levels are visible.
  Decomposes `pivot_cci` into its atomic claims and names the first testable
  hypothesis (bare S1/R1 touch → revert to P). _(no edge claim yet.)_ Also has a
  **"strategy in action"** section (added later): Chart 1 marks real entries/exits
  on ETH Nov–Dec 2024 (buy S1 dips in up-trend, exit at P); Chart 2 = ETH equity
  WITH vs WITHOUT the EMA200 gate (×1.16 vs ×0.20) — visual proof the gate flips
  the strategy's sign, not just tunes it.
- `02_bare_touch_edge` — **H1: bare S1/R1 touch, pooled 165 syms / 2024, vs
  baseline, net 0.15%.** 39.8k long (low<S1) + 37k short (high>R1) events.
  **Asymmetric, as predicted.** After BOTH touches price on avg rises (alt
  up-drift; baseline +0.17% @h24). **LONG (dip-buy) = small real edge:** path
  above baseline, peaks **+0.29% @h13-14** then fades; edge over baseline
  +0.19% gross @h12, **net clears 0.15% cost only in the h12-24 band** (+0.12%
  @h12), ~0 at h4/h8; win barely >51% (mean/skew edge, not hit-rate). **SHORT
  (R1) refuted + anti-signal:** price climbs FASTER than baseline after R1 ->
  short PnL negative & worsening (net -0.20%@h4 -> -0.55%@h24), same shape as
  rsi overbought->short. **But almost certainly NOT pivot-specific:** low<S1 =
  'price ~3% below daily fair value' (headroom to P +3.1% median) = just a dip;
  the net edge is in the `dump`/`rsi` magnitude range. Decisive control pending
  -> `03`: matched-magnitude (S1 touch vs size-matched rolling-mean drop). If no
  lift, H1 = magnitude and the 1.22 lives in the filters (likely EMA200 gate).
- `03_full_strategy_baseline` — **BASELINE: the frozen champion (best version,
  cci_exit=40) over its full harness window 2024-01->2026-01.** Imports the REAL
  strategy code. **(A) BTC/ETH real config: modestly positive & smooth** —
  combined +8.9%, Sharpe 0.57 (simple sim), maxDD -7.3%, 194 trades, 15/24 green
  months, balanced long/short. Genuine small edge on majors. **(B) whole
  universe (148 alts standalone): LOSES hard** — 38% profitable, median -9.5%,
  because alts were massacred 2024-26 (median coin buyhold -75%) and a
  long-biased dip-buyer catches falling knives; median 59 shorts vs 34 longs yet
  still loses (R1 shorts run over by bear bounces = nb02 anti-signal over 2yr).
  **(C) clusters = survivor-vs-corpse, NOT a vol band:** cl1 (n=63, coins down
  only -30%) +9.7% / 70% profitable; cl0 (n=83, coins -61%) -22% / 13% profitable
  with win-rate trap (AUCTION win 59% ret -63% DD -70%). **Headline: the BTC/ETH
  restriction is SURVIVAL not preference** (confirms program.md iters 3/8/12) —
  strategy has no defence vs a trending-down underlying. Roadmap: nb04 isolate
  EMA200 gate (H4, prime edge suspect); test long_only on majors; still owe the
  matched-magnitude H1 control.
- `04_ablation` — **leave-one-out: which slice is removable? BTC/ETH, full window.**
  Ablatable copy of the strategy, verified **bit-for-bit** vs real code (caught +
  fixed a swallowed-NameError bug that silently disabled the funding gate). Drop
  each filter, measure Δ vs FULL (Sharpe 0.567/+8.9%): **− funding = the ONLY
  removable slice** (Sharpe→0.586, +9.5%, DD unchanged — the gate blocks
  net-positive longs on majors). **RSI is LOAD-BEARING (user's intuition
  REFUTED):** dropping it → −3.9%/Sharpe −0.15 (admits 33% more, net-losing
  trades); it prunes marginal entries as a redundant oversold confirmation.
  Hierarchy: **EMA200 ≫ CCI-turn > RSI > CCI-level ≫ funding(dead)**; −ema is
  catastrophic (−73%, confirms nb03 that the trend gate carries the edge).
  Nuance: leave-one-out proves RSI matters AT current settings, not that it's
  irreplaceable — RSI & CCI<-80 correlate, so a tighter CCI might absorb RSI's
  job. **Simplified champion = FULL − funding.** Next nb05: can tighter CCI
  replace RSI (so RSI can also go)?
- `05_why_not_all_assets` — **why the edge works on some coins, bleeds on others.**
  Side-split + coin-character analysis on the 148-alt universe. NOT direction/
  regime: winners & losers trade identical blends (frac_up 0.42/0.43, ~35L/58S,
  same ac1/vr6). Losers = coins that collapsed more (buyhold −51% vs −9%); BOTH
  sides fail together; **short side is the structural drag** (aggregate long
  +3.4 vs short −5.2). Real discriminator = the asset's **mean-reversion vs
  trending character**: sorting alts by lag-1 autocorr gives a MONOTONE gradient
  (Q1 most-MR −2.5% → Q4 most-trending −17.3%, short-side PnL +0.01 → −0.10).
  Mechanism: pivot_cci is **stop-less MR** — on trending/breakout coins the poke
  past S1/R1 CONTINUES and the (esp. short) leg gets run over on a fat tail a
  60%+ winrate can't pay for. Vol is the amplifier (corr(total,vol) −0.20,
  strongest single). BTC mean-reverting (top ⅓); ETH middling, carried by
  EMA-gate+its uptrend. **Answer: edge is narrow BY CONSTRUCTION (MR-in-trend),
  generalizes only as far as an asset is low-vol+mean-reverting+not-collapsing;
  half the market on the wrong side is normal (a spectrum), no informed-
  participant story needed.** All corrs weak individually (−0.12..−0.20) = no
  single clean flag. Next nb06: test user's win-rate idea (likely win-rate trap;
  tail & coin-selection are the real levers).
- `06_curated_mr_universe` — **improvement dir: causal MR-universe selection.**
  Select most-mean-reverting coins by TRAILING 60d lag-1 autocorr (no lookahead),
  monthly rebalance. **(1) Filter is causally real:** MR top-20 +1.1% > FULL
  -8.6% > TRENDING top-20 -17.4% (monotone) - nb05 mechanism exploitable, K=20
  sweet spot; vol-screen does NOT help (low-vol alts = dying/illiquid). **(2) But
  no alt basket beats majors** in the alt bear (+1.1% vs BTC/ETH +8.0%). **(3)
  Prize = combo BTC/ETH 70% + MR-alt sleeve 30%:** +6.1%/Sharpe 0.51/maxDD
  -5.7%/tip 40% vs majors +8.0%/0.55/-7.3%/tip 8% - trades tiny Sharpe for 5x
  activity + lower DD, targeting champion's real binding constraint (composite
  time-in-pos<20% penalty). **Candidate to GRADUATE, not proven** (sim Sharpe !=
  harness composite). Next: user ports combo to strategies/ + runner.iterate.
