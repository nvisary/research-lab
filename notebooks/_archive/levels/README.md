# levels — manual research journal (S/R reversal / bounce from levels)

Sibling sandbox to `notebooks/pump/` and `notebooks/dump/`. Same playbook
(`../pump/HOW_WE_WORK.md`): one small honest step at a time, baseline +
pooling, distrust win-rate, mind ~0.1–0.2% costs, **flag lookahead — especially
in how a "level" is defined**. Helper and data loader are shared (`_lab.py`
copied from pump). Plots end with `show("name")` → read `_out/name.png`.

## The question

Trade **reversals from points of interest** — a bounce off support or a
rejection from resistance. Unlike pump/dump (trigger = a sharp 15-min move),
here the trigger is **proximity of price to a historically significant level**.

## The central methodological problem

A "level" is a place the market *remembers*. The naive way to find one — pick
local extrema of the chart — is exactly the **anchor-selection lookahead** that
killed the pump study: a local high is only knowable N bars *after* it forms.
So every level-detection method MUST be **causal**: a level is visible only from
past bars, as of the current bar's close.

## Research arc

**Phase 1 (here): compare level-detection METHODS, not yet a strategy.**
Different methods draw different levels on the same chart. Before we can test
"do reversals work", we must see and score the level finders themselves:
- Williams fractals (k-bar pivots, confirmed with a lag)
- Swing pivots (wider k)
- Donchian rolling max/min (`.shift(1)`)
- Volume / touch profile (HVN zones over a trailing window)
- (later) pivot clustering into horizontal lines

Evaluation: (a) **visual** overlay; (b) **level density** (a method that draws
100 lines is always "near a level" → useless); (c) **reaction test** — forward
return after price touches a level vs a baseline (random level / random minute).

**Phase 2 (later): the reversal strategy** on whichever finder(s) score best.

## Notebooks
- `00_levels_catalog` — visual catalog of 4 causal level finders (fractals k=2,
  swings k=10, Donchian N=48, volume profile) on ETHUSDT 1h. Findings:
  **fractals k=2 = noise** (pivot on ~27% of bars → price always "near a level");
  swings k=10 sane (~7%) and pivots visibly **stack** into zones; Donchian is a
  trend channel (touch = new N-bar extreme = momentum, not a bounce); volume
  profile marks value/consolidation zones, not turning points.
- `01_pivot_clusters` — method 5: cluster confirmed k=10 pivots into horizontal
  **zones** (strength = #touches), pooling highs+lows (polarity flips). **Lesson
  (bug caught honestly):** first clustering chained pivots by nearest-neighbour
  gap → one zone drifted to 8% wide (2505–2703, "46 touches") = the whole range,
  not a level. Fix = **anchor-bounded** clustering (join only if within tol of
  the zone's anchor → zone ≤ tol wide). Corrected: 78 pivots → 14 tight zones
  (≤0.6%), strongest 7 touches @ ~2652. tol & min_touches are the two knobs.

- `02_reaction_bakeoff` — causal engine: active levels → approach-touches →
  signed forward return vs baseline (drift), + density. 4 methods, 47 syms,
  2024, 1h, 162k events. **KEY NEGATIVE RESULT: bare proximity to a level is NOT
  an edge.** All edges @12h within ±0.05% (vs +0.09% drift, ~0.15% cost) and
  mean/std ≈ 0 — indistinguishable from a random entry. Engine sanity OK:
  fractals_k2 density 81% (=noise, always near a level); donchian_48 the only
  clearly NEGATIVE (−0.05%@12h, −0.22%@24h → touching a Donchian extreme =
  momentum/breakout, bounce fails); clusters lowest density (8%). **Mechanism:
  the touch test averages bounces AND break-throughs → cancels to ~0. The level
  is a "where to look" filter, not a signal.** Edge (if any) must live in a
  REJECTION CONFIRMATION (level held / rejection candle / failed break), not
  mere proximity.

- `03_rejection_triggers` — does a rejection CONFIRMATION rescue it? Same engine,
  level fixed (clusters, swings_k10), trigger varied: bare / wick (close in far
  third) / cbi (pierce + close back inside). 161k events, 2024, 1h. **SECOND
  CLEAN NULL: confirmation does NOT help.** Best = clusters+cbi +0.025%@12h
  (ms 0.009) — still ~6× below the 0.15% cost, mean/std ≈ noise; swings actually
  goes negative with wick. Candle shape at a level carries no edge.

- `04_classifier` — the pump/dump rescue tool (HGBR filter on 21 causal features:
  speed r1..r24, signed approach, ATR/rvol, RSI, vsurge, range position, dist_ma,
  candle shape, level strength, side, trigger flags). 45k cluster-touch events,
  **train 2024 → test 2025-26** (answers "train in 2024, useful in later years?").
  **DEFINITIVE NULL: OOS corr −0.014; deciles flat/non-monotonic; selectivity
  curve INVERTED — the model's most-confident picks are the WORST (top 5%
  −0.25%, top 15% −0.11%), nothing reaches the 0.15% cost line.** Importances
  ~0 (max 0.003), half negative. Answer: NO — a 2024-trained filter is not better
  (worse) than random in 2025-26. Why it works for pump/dump but not here: there
  the classifier RANKS events that already have edge (deep dumps bounce +4%);
  level touches have ZERO base edge → nothing to rank, model learns 2024 noise
  that doesn't recur.

- `05_phenomenon` — REFRAME (user): study levels as a phenomenon, not a strategy
  ("self-fulfilling prophecy" — people watch levels, so the market reacts there).
  Control = REAL cluster zones vs FAKE (same count/timing/strength, price
  randomised from close[a:b]). Measure volume surge + post-touch |move|, no PnL.
  **FIRST NON-NULL: a real ATTENTION FOOTPRINT.** Volume at real-level touches
  is clearly elevated vs fake (median ×weekly-vol 1.33 vs 1.06; mean 3.25 vs
  2.50) at every strength bin. **But it does NOT amplify movement** — post-touch
  |move| identical/slightly lower (2.59 vs 2.68%), distributions overlap. Reading:
  a level is a **liquidity magnet** (more trades, absorbed), not a volatility
  trigger → consistent with no directional OHLCV edge AND with the real signal
  living in microstructure (order-flow: is the volume buying/selling, does the
  level hold or get eaten). Strength dose-response NOT clean (touch-count proxy
  flat/noisy). **Caveat:** location confound — fake levels drawn from close
  concentrate mid-range, real pivots sit at range edges; edge-of-range may be
  higher-volume regardless of "memory". Must rule out before claiming attention.

- `06_confound` — rule out the location confound behind nb05's volume footprint.
  (1) Confound is REAL: pooled vsurge by position-in-range is U-shaped (range-high
  1.46, low 1.15, middle ~0.91). (2) matched-fake = random levels with the real
  levels' position-in-range distribution (permuted). **VERDICT: nb05 footprint =
  location artifact, NOT memory.** real vsurge 1.33 ≈ matched 1.38 (matched even
  higher), both ≫ naive 1.06. Once location is matched, a remembered pivot price
  carries NO extra volume. The "attention footprint" dissolves under the proper
  control.

- `07_speed_at_levels` — (user's idea) does price VELOCITY (|log-return| per bar)
  change near a level? Tests deceleration/absorption. Cross-TF: levels on 1h,
  speed on 1h AND 1m, real vs matched-fake control. **NULL: speed is FLAT vs
  distance-to-level on both TFs** — no dip at dist→0. real sits uniformly ~3-5%
  below matched at ALL distances (a global calmer-regime offset, not proximity).
  Faint non-robust flicker: on 1m real has its lowest speed in the closest bin
  (6.40bp@0.08% vs ~6.6 further out) while matched peaks there — the only
  direction-consistent hint all study, but ~3% and within noise. Exactly what
  WOULD live in microstructure, not candles.

- `08_fractal_clusters` — (user's idea) cluster DENSE k=2 fractals by distance N
  (centroid-linkage, no chaining); strength = #fractals merged → high-resolution
  salience. Visual: confluence nails the obvious horizontal levels (ETH strength
  23 @2630, 19 @2420). **FIRST REAL SIGNAL: a monotone directional dose-response.**
  Signed 12h edge rises with strength: 3-4 +0.012% (ms .003) → 5-6 +0.038 → 7-9
  +0.053 → 10-13 +0.101% (ms .032), then 14+ collapses −0.036% (n=957, noisy).
  Stronger confluence → more bounce, clean over 4 bins (n 26k→3k) = the SFP
  prediction. **BUT still sub-cost**: best (10-13) +0.101% gross = −0.049% NET of
  0.15%, ms tiny. Volume: real BELOW matched & falling with strength (no attention
  footprint — effect is purely directional, not crowd-rush). First non-null
  worth pushing: better exit/horizon on STRONG levels may cross cost.

- `09_topM_levels` — (user's idea) cap active levels to the top-M strongest
  (realistic: a trader watches a few) + forward-return path by horizon, split
  **train 2024 / test 2025-26**. Cap helps slightly IN-SAMPLE (train ms: M=8
  0.028 > uncapped 0.006) but **does NOT transfer OOS**: test net@12h −0.208
  (M=3) … −0.148 (M=8), ms ≤0, and tighter caps are WORSE OOS. Forward path:
  real never approaches the 0.15% cost line on either period and the real−matched
  gap collapses in test. **VERDICT: the nb08 confluence-strength signal was an
  IN-SAMPLE (2024) artifact** — a proper train/test split kills it (same discipline
  that killed the pump +77% mirage). Cap idea is sound; the edge underneath isn't.
  Closing viz: top-5 active levels drawn as track-linked lines that START when a
  level enters the top-5 and STOP when it's booted/ages out — the active set
  visibly drifts with price (old upper levels die on the Aug drop, new ones form
  near 2600); thickness = strength. Plus a zoomed 1m view (price on 1-minute
  bars, levels from 1h, ~3wk window): levels sit at the correct PRICES (Y); the
  apparent rightward shift the user noticed is the causal confirmation lag (a
  confluence level enters top-M only after its fractals stack, i.e. after the
  swings that seeded it), not a data/index shift — verified 1m & 1h both UTC-indexed.

- `10_trendlines` — (user's idea) DIAGONAL trendlines: ray through the two most
  recent same-type confirmed pivots (rising lows = up support, falling highs =
  down resistance), projected forward, alive until a close breaks it. Touch →
  signed fwd return (up-support→long, down-resist→short) vs matched control
  (parallel line, same slope, random price shift). Visual: rays look like proper
  trader trendlines (not a construction artifact). **NULL, like horizontals:**
  real net@12h −0.167% (train) / −0.181% (test) — WORSE than the matched control
  (−0.071 / −0.129) and below zero; per-side signs flip across train/test. A
  diagonal level carries no edge beyond "any sloped ray in a trending market."

- `11_trendlines_1m_bounce` — (user's idea) trendlines on 1m, P(bounce) when
  price returns to the line IN A TREND (SMA gate). Two-barrier metric: within 2h,
  does price move +0.4% with-trend (bounce) or −0.4% (break) first. Real vs matched
  parallel line, train/test. **NULL: P(bounce) ≈ 50-53%** (real train 50.2 / test
  52.8; matched 49.1 / 50.9) vs the **68.8% breakeven** needed at ±0.4%/0.15%-cost.
  A coin flip. Real beats matched by a consistent but economically irrelevant
  ~1.5-2pp (the same "whisper" of non-randomness seen throughout, never tradeable).
  Bounce off a trendline is not more likely than off any sloped ray; retail sees
  survivorship (the lines that held) in hindsight.

## State of the idea — REFUTED in OHLCV (in-sample mirages caught by controls)
Exhaustive sweep, every angle with a control: proximity (nb02), candle
confirmation (nb03), ML filter (nb04), volume footprint (nb05→confounded by
location nb06), velocity (nb07), fractal-confluence strength (nb08→**in-sample
only**, killed OOS by nb09), top-M cap (nb09). **No tradeable, OUT-OF-SAMPLE edge
in 1h/1m OHLCV.** Twice a signal appeared (volume nb05, confluence nb08) and both
times a proper control (location-match, train/test split) dissolved it. This is
the rigorous result and it strengthens the microstructure thesis: every angle
candles can express has been checked and is empty; a real level signal (if it
exists, per the self-fulfilling-prophecy intuition) lives in order flow — book
depth, taker side, level held-vs-eaten — which OHLCV cannot see (out of scope).
One clean OHLCV idea remains untested: round-number levels (psychological
salience, no swing-location confound). Otherwise the OHLCV line is closed.
