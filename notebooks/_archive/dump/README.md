# dump — manual research journal (dump reversion / bounce)

Sibling sandbox to `notebooks/pump/`. Same playbook
(`../pump/HOW_WE_WORK.md`): one small honest step at a time, baseline +
pooling, distrust win-rate, mind ~0.1–0.2% costs, flag lookahead. Helper and
data loader are shared (`_lab.py` copied from pump). Plots end with
`show("name")` → read `_out/name.png`.

## The question
Mirror of the pump study. User's first hypothesis: **short *in the direction*
of a dump (continuation)** when trailing-15m return ≤ −1 / −5 / −7%.

## Notebooks
- `00_dump_event_study` — pooled event study, 50 symbols, 2024 only, first-trigger
  entry (lookahead-free). **Hypothesis REFUTED:** price does NOT continue down —
  it **bounces UP** (mean reversion). Short loses everywhere; bigger dump = bigger
  bounce (−5%/15m → +1.6%/4h, −7%/15m → +3.8%/4h; −1% ≈ baseline = dead zone).
  Correct side is **LONG the bounce**, not short. Unlike pump-fade, the edge is
  NOT front-loaded into the trigger bar (close vs next-bar entry near-identical),
  so it's robust to fill timing.

- `01_long_bounce` — honest LONG-side measurement (50 syms, 2024, entry +1 bar,
  net 0.15%). **LONG works:** no-stop, exit 240m — −5% mean +1.48% (win 60%,
  m/std 0.256); −7% mean +3.60% (win 67%, **m/std 0.510** > best pump). Mirror
  sanity holds (long gross ≈ −short from iter00). **Stop HURTS (mirror of pump):**
  any stop cuts mean AND m/std because the bounce comes AFTER a deep dip (MAE
  median −3%, 50% of trades dip <−3%) → a tight stop knocks you out right before
  the reversal. Best risk-adjusted = no stop / very wide. **CRITICAL caveat:
  survivorship bias now works AGAINST us** — dumped coins that never recovered
  (dead/delisted) dropped from data → long edge likely OVERSTATED.

- `02_scale_in` — **(user's idea) scale-in: add long tranches DOWN as the dump
  continues** (mirror of pump-11). Same-exit comparison, net fees. **Beats
  single-shot on every metric:** −7% m/std 0.423→**0.561**, mean +3.25→+3.42, win
  64→74%, q10 −5.77→**−4.28** (tail improves too); −5% m/std 0.197→0.313.
  Fee note: splitting does NOT double fees (notional-based); 2bp/fill slippage
  proxy negligible (~2.6 fills). Win concentrates in multi-tranche = deep/long
  dumps. Caveat: improved q10 may be a survivorship artifact (no dead coins).

- `03_oos_and_portfolio` — **OOS 2025/2026 + full-period portfolio**, same scale-in,
  no retune. **Holds OOS, esp. deep dumps:** −7% net/trade 2024 +3.42 / 2025 +4.29
  / 2026 +3.05 (m/std 0.56/0.33/0.30) — robust across 3 periods; −5% positive but
  decays (m/std 0.31→0.14→0.11, marginal by 2026) → prefer −7%. Portfolio
  $1000/$20/≤50 concurrent: **−7% +80.5% total / +34.9%/yr / maxDD −4.6%**; −5%
  +56.8% / +24.4%/yr / −8.9%. Idealized per-trade sum (NOT realizable) ~+4700%.
  Equity curve is STEPPY — return concentrated in a few market-wide dump episodes
  (short-vol, lumpy, single-window risk). Survivorship still unaddressed.

- `04_montecarlo_survivorship` — **MC stress (inject the missing catastrophic tail)
  + detailed accounting.** Base −7% portfolio: 1302 signals, 1221 taken, fees only
  **$36.63**, net +$804.57 (+80.5%/+34.9%yr), 2.63 tranches, ~4h hold. **Ruin
  boundary (median ann.ret→0):** L=−30% @ p≈10%, L=−50% @ p≈6%, **L=−100% @ p≈3.5%**.
  Clustered deaths (crash day kills open positions): 4–8/yr barely move the median
  (+32–34%) because ~4h holding ⇒ few positions open at once ⇒ short holding
  structurally limits correlated ruin (p05 at 8/yr +17%, worst DD −29%). Caveats:
  `undermeasurable=0%` IS the survivorship hole (no dead coins in data → true p
  unmeasurable here); clustered model (point-kill) understates correlated risk.

- `05_full_universe_deathrate` — **empirical death-rate on full 173 universe (incl.
  32 dead/delisted coins) + corrected portfolio.** −7% events: 4375 (14.8% on dead
  symbols). **Empirical p tiny:** halt <240m = **0.02%** (1 event), non-recovery
  −30%/24h = 0.96%, −50%/24h = 0.07% — all far LEFT of the ruin boundary (3.5% @
  −100%). Mechanism: 4h window closes BEFORE the slow delisting; instant mid-dump
  halts almost never happen. Corrected portfolio with dead coins included barely
  moves: alive-only +95%/+41%yr/DD−5.3%; full halt=−100% +89.6%/**+38.6%yr**/−6.4%
  (only 2pp gap → 1 halt). Caveats: 32 "dead" overcounts true catastrophes (benign
  migrations ≠ rug) so real p even lower; can't see pre-2024 delistings; **capacity:
  173 syms on $1000/$20 skips 40% of signals**; unmodeled out-of-sample fat tail.

- `06_classifier` — **(mirror of pump-13) feature-based classifier works OOS.**
  14 causal features at the FIRST trigger (r1..r60, accel, surge, volreg, rng15,
  dist-hi/lo, down-streak), label = scale-in net PnL, time-split 60/40. OOS corr
  +0.117 (vs pump +0.30) but **deciles monotonic** (worst +1.4% → best +9.24%,
  win 84%). Filter lift OOS: trade-all +4.89% (m/std 0.374) → top-30% +7.25%
  (m/std 0.573). Top features: r60 (dominant), r5, r3, rng15, dstreak; surge/accel/
  hi_d useless. Caveats: single split (need walk-forward), test era stronger
  (abs inflated). Saved `dump_trades.parquet` + `dump_test_pred.parquet`.
- `07_compounding` — **(user's idea) reinvestment / compound interest.** Full
  universe, 2.32y, event-driven cash sim. A Fixed $20: +96%/CAGR 33.7%/DD −6.4%;
  **B Compound SIZE (2%/trade): +147%/CAGR 47.6%/DD −7.8% (best balance)**;
  C Compound SLOTS: +124%/CAGR 41.7%/**DD −9.3% (worst — crams more positions into
  correlated clusters)**. Lesson: reinvest lifts CAGR but deepens DD; size-compound
  beats slot-compound (no extra correlated exposure). Classifier filter + compound
  (OOS test): top-50% CAGR +44%/DD −5.0% > trade-all +41%/−6.9% (higher return AND
  lower DD). Reinvest is a lever, not magic; compounding into clusters amplifies tail.

- `08_black_swan` — **catastrophic-cascade stress.** Realized −6% DD is a FLOOR:
  2024-26 has NO systemic cascade — all correlated days were V-shaped (algo won/
  flat: 2025-10-10 +16.7% on 319 dumps; yen-carry 2024-08-05 +0.3%). Worst real
  cluster 2025-02-02 −11%/trade. **Damage depends on crash SPEED vs our 30-min
  tranche window:** slow grind −50%/4h → NO trigger (safe); pure flash −50%/15m →
  only −7.5% (averages in near bottom); **mid-speed deep cascade (LUNA −90%/3h) →
  −88% position** (tranches load near top, then craters) — where scale-in w/o stop
  kills; −50%/60m → −34.6%. **Leverage = ruin:** move×lev ≤ −80% wipes account (2x
  ruins at −40%, 5x at −16%) → 1x only. Base rate ~1 major cascade / 1.5–2yr (COVID/
  LUNA/FTX all out-of-sample). Mitigations (unbuilt): 1x, hard-catastrophe stop,
  market-wide regime kill-switch.

- `09_tail_protection` — **catastrophe stop + market-wide kill-switch (default-on
  limit until manual launch) + the "stairs" answer.** Stairs explained with data:
  **97% of PnL from top-20 days, 38% of days have zero dumps** — dumps are
  correlated (risk-off hits all alts at once) vs idiosyncratic pumps. **Uncomfortable
  truth: in-sample, tail protection COSTS return and barely cuts DD** (stop even
  raises it −6.3→−11.7%) because every correlated crash in 2024-26 was V-shaped —
  the deep dip a stop cuts always bounced; and high-breadth days ARE the top-20
  profit days. Stop −25% costs −3.2pp/yr (worst −45→−25, hits 2.1%); kill-switch
  K=40 costs −4.5pp/yr. Protection = insurance: premium visible, payout in the
  unsampled LUNA cascade. Recommended default limit: −20% stop + KS K=40 →
  +30.2%/yr, maxDD −9.9%, worst −20.2% (~−10pp/yr premium, loosen under manual
  supervision).

- `10_adaptive_fill` — **(user's idea) adaptive fill schedule by dump path.** Three
  schemes: A time-fixed (baseline), B price-step (tranche when close ≤ last·(1−2%)),
  C class-conditioned (sharp→time, grind→price). **B wins clearly:** per-trade
  +3.82→+5.23% (m/std 0.375→0.397, q10 −5.55→−5.30); portfolio +34.4→**+53.2%/yr**
  with **lower DD −5.5%**. Mechanism: lower avg entry (−2.1 vs −1.1%), fewer/more-
  selective fills (1.76 vs 2.81). **The path self-classifies → pure price-step beats
  class-conditioned; no separate classifier needed for FILL CADENCE** (iter06 stays
  for trade SELECTION). Honest caveat: price-step slightly WORSE in extreme fast
  crashes (N_MAX=10/Δ=2% exhausts tranches in the top −20% of a deep flash:
  −50%-no-bounce time −7.4% vs price −26.3%; under-bets quick V-reverters) — but
  negligible at realistic cascade/LUNA, and the catastrophe stop handles the true
  tail. **Adopted: price-step = default fill, iter09 limits on top.**

- `11_final` — **CAPSTONE: all improvements combined, full universe 2024-2026,
  walk-forward classifier, reinvest.** Progression (fixed $20): v0 time +81%/Sharpe
  2.05/DD−6.2% → v1 +price-step +124%/2.03/−5.5% (Calmar 7.64) → v2 +classifier(WF)
  +120%/2.10/−5.5% → v3 +tail-limits +98%/1.88/−7.9% → **FINAL +reinvest:
  +151%/CAGR 48.8%/Sharpe 1.91/Sortino 2.82/maxDD −10.4%/Calmar 4.69** (2015 trades,
  win 62.9%, mean +2.64%, worst −20.2% stop, fees $97). WF OOS corr +0.227.
  **HONEST: "all stacked" ≠ best in-sample curve** — best risk-adjusted is v1/v2
  (Sharpe ~2.1, Calmar 7.6, DD −5.5%); tail-limits + reinvest WORSEN in-sample DD
  (−5.5→−10.4%) deliberately (insurance vs unsampled cascade; growth vs smoothness).
  Config = choice of philosophy, not "max everything."

## State of the idea
Dump = mirror of pump: both mean-revert. **Long the bounce after a deep dump
(−7%/15m) is strong and holds OOS** (3 periods, +3–4%/trade net, m/std ~0.3–0.56;
portfolio ~+35%/yr, maxDD −4.6%). Unlike pump-fade it does NOT want a stop and is
NOT fill-timing-fragile; scale-in (averaging down) improves it further. −5% is
weaker/decaying. Returns are lumpy (concentrated in market-wide dump clusters). **Survivorship is
SMALL (iter05): empirical death-rate ~0.02–1% ≪ ruin boundary 3.5%; including the
32 dead coins moves annual return by only ~2pp. The 4h holding window structurally
exits before slow delistings.** Honest residual risks: out-of-sample fat tail
(market-wide mania, only stressed parametrically in iter04); capacity (40% signals
skipped at 173 syms on $1000/$20). Classifier (iter06) works OOS and lifts per-trade edge + smooths compounding;
reinvestment (iter07) raises CAGR to ~+45%/yr but deepens DD (size-compound >
slot-compound). **Black-swan (iter08): the smooth curve has never met a real
cascade — realized −6% DD is a floor; a mid-speed deep cascade can take −35..−88%
of deployed capital at 1x, and any leverage = ruin.** Tail defenses built (iter09): a −20% catastrophe stop + breadth kill-switch are
the recommended default-on limits (cost ~10pp/yr in-sample, real payoff only in an
unsampled cascade). Adaptive fill (iter10, price-step) and walk-forward classifier (iter11) DONE and
folded into the capstone. **Final config: CAGR ~49% / Sharpe 1.91 / DD −10.4%
(growth+insurance), or v1/v2 ~41% / Sharpe 2.1 / DD −5.5% (smoothest).** NOT yet:
combine with pump (uncorrelated → expected lower DD; user wants this next-ish),
anchor-selection sanity, graduation to strategies/ harness + live paper bot with
default-on tail limits.
