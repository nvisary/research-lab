# RSI & RSI-divergence research

Manual research line (sibling of `notebooks/pump`, `dump`, `levels`) — same slow,
honest, teaching-oriented playbook. **Read `../pump/HOW_WE_WORK.md` first**, then
this journal, then the latest numbered notebook.

Helper: `_lab.py` (import with `sys.path.insert(0,'.'); from _lab import *` when
cwd is this folder). Adds `rsi()` (Wilder) and `pivots()` (causal swing detection)
on top of the standard loader. Plots end with `show("name")` → `_out/name.png`.

## The question

Is there a tradeable edge in **RSI** and especially **RSI divergence** (price and
RSI disagreeing about a trend) on crypto perps? RSI is the most-cited retail
reversal tool; the levels line just closed showing retail S/R has no OHLCV edge.
We test RSI with the same discipline: baseline, pooling, costs, and above all
**no anchor-lookahead** (a swing pivot is only confirmed `k` bars late).

## Central trap (carried over from pump & levels)

A divergence is defined between two **swing pivots**. A pivot at bar `i` is only
knowable at `i+k`. Any "divergence existed here" we draw in hindsight is anchored
on confirmed extremes — the same selection-lookahead that turned pump's +77% into
a loss. Descriptive notebooks may draw pivots in place; **any edge claim must
enter only at pivot-confirmation (`i+k`) or later.**

## Notebook journal

- `01_rsi_and_divergence` — INTRO. What RSI is (intuition + Wilder formula) and
  what divergence is (regular/hidden, bullish/bearish). RSI of BTCUSDT drawn over
  1m / 15m / 1h / 2h(resample) / 4h to show how timeframe changes the indicator.
  Existing regular divergences detected with causal pivots and annotated. _(no
  edge claim yet — this is the vocabulary + "what does it look like" step.)_
- `02_bare_rsi_edge` — **FIRST NON-NULL (asymmetric).** Bare RSI(14) 30/70 cross,
  15m, **2024 only** (2025 sealed for OOS), pooled 165 symbols (n≈73k each side).
  Causal entry at the cross-bar close, forward return vs unconditional baseline,
  0.15% cost. **OVERSOLD<30 → LONG works:** edge above baseline at every horizon,
  monotone; clears cost from ~4h on (net h16 +0.03%, h32 +0.12%, h48 +0.21%,
  h96 +0.11%); mean/std ~0.10 @ h48. **OVERBOUGHT>70 → SHORT loses:** mean PnL
  negative & worsening with horizon (−0.37% @ h96), edge vs baseline ≤0, net
  deeply negative — alt up-drift + a fat up-runner tail (median short PnL is
  POSITIVE while mean negative = the classic win-rate trap). Headline: **RSI is a
  dip-BUY signal, not a sell signal, on 2024 alts.** Caveats: oversold ≈ "a recent
  sharp drop" → must control vs plain magnitude dip-buy (next); entry-at-close
  optimism (1-bar-delay check pending); one regime; overlapping windows.
- `03_rsi_vs_magnitude_multitf` — **RSI level/cross REFUTED as an independent
  signal — the edge is the DROP.** (A) Multi-TF (1m/5m/15m/1h/4h, hours-horizons,
  net after cost, stride-55): oversold→long net-positive only on 15m (peak +0.22%
  @12h) + faintly 5m; **1m net-negative everywhere** (cost eats the tiny bounce);
  1h/4h negative; overbought→short **red on every TF×horizon** (universal loser).
  (B) Control on 15m, 4h fwd: head-to-head net edge — plain `drop≤−5%/14bars`
  **+0.26%**, `RSI<30` state **−0.11%** (loses), `RSI<30 AND drop≤−5%` **+0.26%**
  (RSI adds +0.002% = nothing). Matched-magnitude: at ordinary drops the NOT-
  oversold line is *above* oversold; RSI<30 only wins in the rare <−12% dump bin
  (= deepest magnitude). nb02's positive cross = it marked a fresh drop, narrowly
  inheriting the magnitude edge. 1-bar-delay negligible (−0.019%). **Net: bare
  RSI = a worse proxy for move magnitude (already owned by the `dump` line).
  Divergence (momentum≠price) is a different construct, still the open question.**
- `04_divergence_edge` — **divergence event-study, lookahead-free (enter at i2+k).**
  15m/2024, 165 syms, pivots k=6; split each price pattern by RSI diverged vs
  confirmed (the deciding control). **BEARISH HH→SHORT refuted + anti-signal:**
  shorting a bearish divergence loses (−0.19% @12h) and is WORSE than shorting a
  non-divergent HH (div−conf −0.16%) — after a bearish div price rises *more*.
  **BULLISH LL→LONG = sub-cost whisper:** diverged beats matched control at 1–2h
  (div−conf +0.03/+0.05%, beats baseline) — direction-consistent w/ theory — but
  every net edge is negative after 0.15% cost (best −0.090% @2h), and at 8–12h the
  *confirmed* LL wins (+0.122 vs +0.034 = deeper capitulation = magnitude/`dump`).
  Same shape as levels: real>control whisper (~3–5bp), never tradeable. **One fair
  shot left: 1h/4h (divergence is classically a higher-TF tool; nb01 showed cleaner
  divs there). Then close.**
- `05_rsi_signal_classifier` — **(user's idea, off the planned path) port the pump-13
  / dump-06 HGBR classifier to filter RSI signals.** Signal = RSI<30 cross→LONG 15m,
  label = net 4h pnl, 14 magnitude/shape feats + 3 RSI feats, time-split 60/40 in
  2024 (2025 sealed). **Two clean negatives: (1) RSI features add nothing** — OOS
  corr magnitude-only −0.013 vs +RSI +0.000; importance = volreg(.21)/r48(.14)/
  hi_d(.05) (magnitude), RSI feats ~0 at the bottom. **(2) The technique doesn't
  transfer** — OOS corr ≈0 (vs dump +0.117, pump +0.30), deciles **U-shaped not
  monotone** (dec0 +0.53%, dec9 +0.93%, middle ~0). Apparent filter lift (pred>0
  +0.68% vs all +0.29%) rides the good top deciles in a favorable Q4-2024 dip-buy
  window, not a robust ranking → untrustworthy on one split. Why weaker than dump:
  −7%/15m pre-selects deep dumps w/ separable dispersion; RSI<30 fires on a 73k
  mushy population the GBM can't rank. **Net: classifier opens no RSI edge; only
  magnitude/vol-regime structure (owned by `dump`), RSI = dead weight.**
