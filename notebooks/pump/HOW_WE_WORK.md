# HOW WE WORK — manual research playbook

This file is for **future Claude** (and the user) to restore the *way we work*
in `notebooks/`, not just the findings. The user finds this style highly
effective and wants it reproduced. Read this first, then `README.md`, then the
latest numbered notebook to see where the research is.

---

## The collaboration contract

The user is learning quant research hands-on. **Pace and clarity beat speed.**

1. **One small step at a time.** Never dump a giant analysis. Pick the single
   most informative next experiment, run it, explain it, then propose the next.
2. **Explain slowly, in plain language.** Assume the user is smart but not yet
   fluent in the jargon. When introducing a term (dedup, z-score, CDF, q10,
   mean/std), explain *what it is and why it matters* with a concrete intuition
   or analogy. The user explicitly asked: "медленнее и менее технически".
3. **Confirm understanding loops.** When the user paraphrases an idea back
   ("правильно ли я понял?"), validate the correct parts explicitly and gently
   correct the rest. This is how they build intuition — honor it.
4. **Take the user's ideas seriously and test them honestly**, even when you
   suspect they won't work. The user's "scale order" and "add-on-rise ladder"
   ideas led to the best teaching moments. Run the real experiment; let data
   decide. If an idea is good, say so; if the data refutes it, show *why*.
5. **The user said: "сразу отвергай мои жалкие попытки если они тупые".**
   So: be honest, never flatter a bad idea — but explain the mechanism behind
   the verdict, don't just say no.

## Scientific principles we always apply

- **Always compare against a baseline.** A conditional number is meaningless
  alone. "49% up after volume spike" only means something next to "49.8% up at
  any random minute". Bake the baseline into every test.
- **Pool for sample size.** One symbol = anecdote (n≈18). Pool across many
  symbols to get n in the hundreds before trusting a shape. We literally watched
  a single-symbol "continuation" finding evaporate once pooled (nb01 vs nb04).
- **Win-rate is a trap; look at the whole distribution.** Report mean, median,
  win%, std, and a tail quantile (q10). We found an 83%-win-rate rule with
  *negative* expectancy. Median vs mean tells you the skew direction.
- **Risk-adjusted, not raw.** mean/std (a per-trade Sharpe-like ratio) is the
  decider, not mean alone. Smoothing the tail is worthless if it kills the edge
  faster (that's what scale-in did; the stop-loss won on mean/std).
- **Mind costs.** Alt/memecoin perp round-trip ≈ 0.1–0.2%. An edge below that is
  not real. State it whenever a mean looks small.
- **No lookahead.** Signals must be computable at bar close from past data only
  (e.g. our pump trigger uses trailing 15-min return). Call this out explicitly.
- **Honest caveats every time.** Single period / in-sample / no funding / no
  slippage / overlapping (autocorrelated) events / idle-capital normalization.
  List what we have NOT yet controlled for.

## The mechanical loop (how notebooks get written and read)

Jupyter is installed in the dev group (`uv add --group dev jupyterlab ipykernel
nbconvert`). The user runs the UI with `uv run jupyter lab`. Claude works
headless:

1. **Write/edit** a notebook (`Write` for new, `NotebookEdit` for cells).
2. **Execute:** `uv run jupyter nbconvert --to notebook --execute --inplace notebooks/<nb>.ipynb`
3. **Read text/tables:** `Read` the `.ipynb` directly — cell stream outputs and
   DataFrames render fine.
4. **Read plots:** base64 PNGs embedded in `.ipynb` are too big for `Read`.
   So every plot ends with `show("name")` (from `_lab.py`) which renders inline
   for the user AND saves `_out/name.png`. Claude reads that PNG file to *see*
   the chart. This is the key trick that makes visual collaboration work.
5. If a long compute is reused across notebooks, cache it to `_out/*.npy`
   (e.g. `pump_paths.npy`) so later notebooks load instantly.

`_lab.py` is a thin wrapper over the canonical `datafeed.loader`
(`ohlcv`, `funding`, `list_symbols`, `coverage`, `show`). Data = Bybit perp 1m
parquet; `tf` resamples on the fly.

## Reporting format (per step)

After each experiment, the response structure that works:
- **Headline result** in one line (the verdict, with the key number).
- **How to read the chart/table** — name the axes, say what blue/red means.
- **What it means** in plain words (the intuition, not the code).
- **Honest caveats** — what's still uncontrolled.
- **Next step**, usually 2–3 options with a clear recommendation and *why*.
Tables and small numbers beat prose. Don't bury the verdict.

## This is research, NOT the strategy harness

`notebooks/` is a free-form sandbox, deliberately *outside* the `runner.iterate`
auto-loop and its KEEP/REVERT/holdout rules (see repo `CLAUDE.md` / `AGENTS.md`).
Here we explore freely across any time period and any symbols. The strategy
holdout discipline does **not** apply to notebooks — but good science discipline
(baseline, pooling, OOS check on a *different period*) absolutely does. When an
idea graduates from here into a real strategy, it moves to `strategies/` and
plays by the harness rules.

## Research progress (keep this current)

The numbered notebooks are the lab journal. Update this list when you add one.

- `00_smoke` — verify the loop (load data + render a plot).
- `01_pump_detect` — v0 pump detector (+5%/15m & 3× volume) + event study, 1 symbol.
- `02_one_pump_and_hypothesis` — one pump annotated (shows dedup); tested
  "2× volume → price up?" → **refuted**: volume is direction-agnostic (~50%).
- `03_direction_continuation` — does the move continue after a volume spike?
  → **reversion, not momentum**: P(continuation) < 50% everywhere; spike
  *strengthens* reversion vs baseline.
- `04_real_pumps_fade` — strict pumps **pooled across 80 symbols** (n=766).
  Average path **peaks at the trigger then fades** ~1.5–2% over hours.
  Tradeable: **short at trigger** → mean +1.47%, median +1.87%, win 64.5%,
  but fat left tail (q10 −6.4%) from runners.
- `05_scale_order` — scaling the entry over time smooths the tail BUT kills
  the edge faster → mean/std *worse*. Edge is front-loaded, so diluting entry
  dilutes edge.
- `06_ladder_vs_stop` — user's add-on-rise ladder vs stop-loss. Ladder gives
  the thinnest tail but guts the mean (front-loaded edge again). **Stop 3% wins
  risk-adjusted** (mean 1.39%, q10 −3.76%, mean/std 0.286 > baseline 0.248).
  Also found an 83%-win / negative-expectancy take-profit (win-rate trap).
- `07_oos_2025` — **out-of-sample on 2025-01..2025-09, 154 symbols (n=2178)**,
  same rule, no retuning. Edge **held and strengthened**: stop-3% mean
  +1.67% (vs +1.39% in 2024), median +1.34%, win 56.6%, mean/std 0.324.
  Path shape identical to 2024. Net ~1.52%/trade after 0.15% cost, **8/9 months
  positive** (only Feb-2025 negative — a correlated market-wide melt-up where
  pump-shorts lose as a cluster). Idealized sum of per-trade PnL +3308% net but
  NOT a real return (ignores trade overlap / capital cap).
- `08_portfolio_2025` — realistic portfolio $1000 / $20-per-trade (2% per pos,
  ~50 concurrent). 2025: **+77% over 9 months, realized maxDD −6.3%**, 7% of
  signals skipped (capacity binds only in clusters = protective). Return scales
  with trade size, drawdown stays ~−6% (skips cap cluster exposure).
- `09_mtm_and_2026` — skeptic checks. (1) TRUE mark-to-market drawdown 2025 =
  **−9.3%** (vs −6.3% realized; deeper but modest, Feb cluster). (2) **2026**
  (Jan-Apr, 140 symbols, n=971): edge holds again — mean +2.05%/trade,
  m/std 0.356; portfolio +43% in 4mo, MTM maxDD −3.6%. Three independent
  periods now confirm. **The real catch is the edge's NATURE: it's short-vol /
  short-gamma** — steady premium, rare big losses; backtest worst case bounded
  by the calm-ish 2024-26 clusters, a true alt-mania could be far worse.
  Survivorship bias likely *helps* us (delisted post-pump coins = missed short
  wins). Still unmodeled: funding, worst-case micro-cap slippage.
- `10_funding` — funding data covers all 173 symbols (4h cadence). For our
  shorts funding is a tiny DRAG (not a help): mean −0.06%/trade (2025),
  −0.13% (2026); ~half the trades cross one 4h stamp, pay/receive ~symmetric.
  Net per-trade after fees+slippage+funding: **+1.46% (2025), +1.78% (2026)**.
  Portfolio with funding: 2025 +74% (was +77%), 2026 +40% (was +43%), DD
  unchanged. Funding is the smallest cost line — edge survives comfortably.
- `09_2_execution_timing` — schematic of WHY the harness verdict is negative:
  the avg pump path peaks at t=0 (our bot's market fill the instant the candle
  closes ≈ close[t] ≈ open[t+1]); by t=+1 (the harness's next-bar-close fill)
  price has already dropped ~0.76%, so the harness shorts that much lower.
  Per-trade: our bot +1.67% vs harness +0.72%. The harness is NOT cheating in our
  favour — it's too pessimistic (1-min decision frequency can't model a
  sub-minute entry). The fade is front-loaded into the first minute.
- **`strategies/pump_fade_xs/`** — ported the rule into the repo harness to
  cross-check the hand-rolled sim. **The cross-check FAILED the optimistic
  assumption:** the harness must `.shift(1)` (enter the bar AFTER the trigger),
  and a **1-bar entry delay halves the edge** (2025: +1.67% → +0.72% gross;
  by +5m it's ~0). The notebook entered at the trigger BAR'S CLOSE — which is
  the local peak AND the same close that defines the +5%/15m condition — i.e. an
  optimistic/near-lookahead fill. Realistic execution + costs makes the edge
  marginal-to-gone (harness Q1-2025: OOS Sharpe ≈ 0, PF ~1.0).
- **LESSON (write this on every future event-study):** when an entry is timed on
  the bar that *defines* the signal, always re-measure with a 1-bar delay AND a
  fill-price haircut before believing the number. "Enter at signal-bar close"
  silently captures the reversion of the very bar that triggered.
- **Nuance (don't over-conclude either way):** the harness shift(1) = a FULL
  1-minute-late entry, which is too pessimistic for a real market order (fills in
  seconds). Fill-price haircut test: if you DO fill near the trigger close, the
  edge is robust to slippage (2025 +1.08% at 30bp/side, +0.68% at 50bp/side).
  The whole edge lives INSIDE the first 1-minute bar (+0m +1.67% vs +1m +0.72%),
  so 1-min OHLCV cannot adjudicate whether a seconds-fast fill keeps it.
- A next-bar-OPEN test *seemed* to rescue it (+1.67% kept) — but that test, like
  the whole cache, was anchored at the MAX-RET minute. The rescue was illusory.
- `09_3_entry_point_lookahead` — **THE DECISIVE FINDING (user-spotted lookahead).**
  The pump condition is true at many consecutive minutes. The dedup anchored each
  event at the minute with the **max 15-min return** — which you can only know
  AFTER the cluster ends (it's ≈ the top, chosen in hindsight). A real bot enters
  at the **first** trigger. Measured on 2025 clusters (3% stop, 4h):
  - FIRST trigger (real bot):       **−0.53%, win 37%**  ← LOSES
  - MAX-RET trigger (notebook):     +1.67%, win 57%      ← hindsight
  - ACTUAL PEAK (oracle):           +2.25%, win 63%
  After the first signal the pump keeps running **+2.74% on avg** for a median
  6 (mean 11) more minutes before topping. Shorting the first signal gets run over.
- **CORRECTED STATUS: the edge was a LOOKAHEAD ARTIFACT.** The entire +1.67% /
  +77% / +74% edifice rested on anchoring entries at the in-hindsight peak of each
  pump cluster. With an honest, realizable first-trigger entry the strategy LOSES
  (−0.53%/trade, win 37%). The harness was right all along — it entered at the
  first trigger, which is why it was negative. The 1-min fill-timing story (09_2)
  was a real but secondary effect operating *inside* the tainted anchor.
- **The live `pump_fade_bot` enters at the first trigger → it trades the LOSING
  version.** Do not trust its (expected-negative) PnL as validation of an edge.
- **META-LESSON:** the deadliest lookahead isn't in a single `.shift()` — it's in
  **event/anchor SELECTION**. Picking "the best minute of the cluster" needs the
  future. Dedup/aggregation that chooses among points by an outcome-correlated key
  (max return) is lookahead even when each point's value is causal. The harness's
  negative cross-check was the truth; the user's reasoning ("we aggregated over
  history → we looked into the future") nailed the mechanism.
- **Possible salvage (non-lookahead):** detect EXHAUSTION in real time — short only
  after the pump stops making new highs (momentum rollover / first lower-high),
  not on the first +5%/15m trip. Untested; the only honest path left.
- `11_scale_in_baseline` — **the salvage WORKS (user's idea).** Lookahead-free
  scale-in: add a short tranche on EACH cluster trigger (ramped, later bigger),
  total notional capped = 1 position, 3% stop on avg entry armed AFTER the cluster
  stops firing, 4h exit. Walks the avg entry up toward the peak. Fixed universe =
  140 alts with full 2024-01..2026-04 coverage. Per-trade NET: 2024 +0.37% (win
  48%), 2025 +0.81% (52%), 2026 +1.13% (51%) — positive every year, strengthening.
  $1000/$20 portfolio **combined 2024-2026: +126% (+42%/yr), maxDD −7.2%**, 21/28
  months green (vs first-trigger −48%). This is the **honest baseline** (no
  lookahead). Caveats: short-vol cluster tail (worst −7% Apr-2024), 1m fast-fill
  assumption, flat slippage proxy (multi-tranche may add a bit), 2024 marginal.
- `12_limit_vs_market` — tested limit-ladder (maker) vs market (taker) entry.
  **Limits LOSE** (+1.21% market → −0.30% limit). Adverse selection: a resting
  sell-limit fills only when price RISES into it → fills 23% in fast-reverters
  (our best trades, under-bet) and 100% in mega-runners (our worst, over-bet,
  −9.21%). **Principle: fade entries want TAKER; the taker fee is the price of
  avoiding adverse selection.** Spend effort on entry TIMING (exhaustion/CVD),
  not order type.
- **Open ideas to improve (user):** (a) LIMIT orders — maker fee ~3x cheaper than
  taker AND a sell-limit ladder up the run-up averages closer to the peak;
  testable on OHLC (a sell-limit at P fills if bar HIGH≥P) — watch adverse
  selection (limits fill on runners, may miss quick reverters). (b) CVD
  (cumulative volume delta) for real-time exhaustion — high ceiling (approaches
  the +2.25% oracle) but needs taker-buy/sell data (aggTrades / live trade WS),
  which our OHLCV parquet lacks; easier to prototype LIVE in the bot.
- `13_pump_classifier` — **(user's idea) feature-based pump classification WORKS
  out-of-sample.** 12 causal features at the trigger (r1..r30, accel, surge,
  vol-regime, range, dist-from-hi/lo), label = scale-in net pnl, time-split
  train 60% / test 40% (train ends 2025-03, test 2025-03..2026-04). OOS
  corr(pred, actual) +0.30; test deciles are MONOTONIC: worst decile −2.35%
  (win 16% = the runners), best +2.49% (win 67%). Trading the top 50% the model
  likes: **+2.26%/trade (win 67%) vs +1.10% trade-all** — ~2x the per-trade edge,
  OOS. Top features: r30, r3, r5 (run shape/speed) + surge1 + dist-from-low; the
  detector's own r15/surge15 are ~useless (constant by construction). Confirms
  there ARE distinguishable 'pump types' and the reverters are separable from
  runners by causal features. Caveats: single time-split (need walk-forward),
  modest corr, test era is the stronger regime, no order-flow features yet.
- `14_walkforward_portfolio` — **(1) walk-forward + (2) filtered portfolio.**
  4 expanding folds all hold (corr +0.27/+0.27/+0.33/+0.28; top-50 beats all in
  every fold) — not a single-split fluke. Filtered ($1000/$20, keep pred>0 = 72%
  of events): **+115% vs +98% trade-all, maxDD −3.8% vs −6.3%** — higher return
  AND lower drawdown (cuts the runner tail). Filtered per-trade +1.73% vs +0.92%.
- **(4) Ported to the live bot** (`C:\projects\pump_fade_bot`): scale-in entry +
  classifier filter. `train_model.py` fits the HGBR on shipped features and saves
  `model/pump_clf.pkl`; the bot computes the 12 causal features live and only
  shorts pumps the model scores > 0, scaling in across the cluster. Replaces the
  old losing first-trigger-unfiltered entry.
- **State of the edge (honest):** lookahead-free, walk-forward-validated,
  ~+1.7%/trade net filtered, ~+68%/yr on $1000/$20 with ~4% DD. Remaining work:
  CVD/order-flow features (needs tick data), live confirmation, regime filter.
