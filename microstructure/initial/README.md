# initial — microstructure research journal (open & understand the data)

First research line on the live Bybit microstructure captures (see
`../README.md` for the collector). Same playbook as `notebooks/` — one small
honest step at a time, baseline + pooling, distrust win-rate, mind costs, flag
lookahead. Plots end with `show("name")` → read `_out/name.png`. Helper is
`_lab.py` (thin wrapper over `microstructure.loader` + `show()` +
book primitives: `mid`, `spread`, `spread_bps`, `depth`, `imbalance`).

## The data (what we're working with)

Live capture from Bybit **linear perps**, four streams per symbol:

| stream | rows/row-shape | key columns |
|--------|----------------|-------------|
| `orderbook` | top-25 *wide* snapshot every ~250 ms | `bid_px_i/bid_sz_i`, `ask_px_i/ask_sz_i` (i=0..24) |
| `trades` | every public trade | `price`, `amount` (base), `side` (aggressor), `id` |
| `ticker` | periodic BBO+ | `last`, `bid/ask(+size)`, `mark`, `index`, `funding_rate`, `open_interest`, volumes |
| `liquidations` | sparse forced exits | `side`, `price`, `amount` (often `None`) |

Every row has `ts_exchange` (event time) and `ts_local` (receipt), both ms.

Sessions on disk (`data/bybit/micro/sessions/`):
- `2026-07-08T19-00-28Z` — empty (failed smoke, 0 rows). Ignore.
- `2026-07-08T19-06-02Z` — 2.9h smoke, 5 syms, 1.1M rows.
- **`2026-07-09T08-03-30Z`** — the real one: **14.3h, 5 syms** (BTC/ETH/SOL/SUI/TRX),
  **4.9M rows**, 525 MB. This is what we explore.

## Notebooks

- `00_open_data` — open the capture and check it's trustworthy. Loads all four
  streams for BTCUSDT, shows columns + real rows, runs sanity checks, plots the
  first picture (mid + tape). **Findings (all green):**
  - BTC/14.3h: orderbook **185.5k** snapshots, trades **1.33M**, ticker **375k**,
    liquidations **175** (sparse, as expected; `amount` is `None` for these).
  - Book cadence **median 260 ms** (p95 358 ms) vs 250 ms target — honest, the
    ~10 ms drift is snapshot+write overhead, no big gaps.
  - Tape is busy: **~1544 trades/min**. Feed lag **median 0.28s** (p95 0.42s) —
    *better* than the ~0.58s the collector README first measured.
  - Mid price `(bid_px_0+ask_px_0)/2` tracks the tape cleanly over the full
    session (62.4k→63.5k range, looks like a real market). See
    `_out/00_btc_mid_tape.png`.
  - **Verdict:** raw material is trustworthy. No signal tested yet.

- `01_cvd_btc` — first order-flow series: **CVD** (cumulative signed taker
  volume) from the tape, BTC/14.3h. `side=buy → +amount`, `sell → −amount`,
  running cumsum. **Findings:**
  - Tape near-balanced over the session: buy 20,561 BTC / sell 19,903 BTC,
    **net +658 BTC (+1.6% of gross)**, buy share 50.8% — calm, no persistent
    one-sidedness. CVD path swung −755 … +899 BTC.
  - CVD tracks price cleanly (see `_out/01_btc_cvd_mid.png`): CVD trough at the
    ~12:30 price low (−750), CVD spike to +900 at the ~18:00 breakout. The four
    big legs of the day line up.
  - **Contemporaneous** corr(ΔCVD, Δmid) @10s = **+0.46**; bins with net buying
    averaged +6.7 USDT, net selling −6.2 USDT. Consistent sign.
  - **Verdict:** CVD computes cleanly and co-moves with price — but this is
    *contemporaneous & partly tautological* (market orders move price by
    definition). NOT yet evidence of prediction.

- `02_cvd_oi_bridge` — **the bridge**: price + CVD (flow) + OI (stock) on a 1-min
  grid (OI updates ~9s), each minute classified by `sign(Δprice)×sign(ΔOI)`.
  BTC/14.3h, 858 bins. **Findings:**
  - The 2×2 splits cleanly and sensibly (mean ΔCVD in BTC / minute):

    | price·OI | minutes | mean ΔCVD | reading |
    |---|---|---|---|
    | up · OI+ | 217 (25%) | **+11.4** | new longs opening |
    | up · OI− | 211 (25%) | +10.0 | short covering |
    | down · OI+ | 197 (23%) | −4.4 | new shorts opening |
    | down · OI− | 233 (27%) | **−13.2** | longs closing / liq |

    CVD sign flips with price (within-bin, partly tautological); **OI sign is the
    new information** — it separates open from close.
  - **Real (non-tautological) result** (`_out/02_price_cvd_oi.png`): the two big
    rallies had *different DNA*. The ~13:30 midday pop = price↑ **OI↑** (new
    longs, froth). The ~16:00→18:00 recovery off the low = CVD ripped +900 BTC
    but **OI collapsed 56.0k→55.0k = short-covering squeeze**. Same price shape,
    opposite positioning — invisible on OHLCV, obvious with OI.
  - Session net: price **+349 USDT**, **OI −1055 BTC**, CVD +649 BTC → the day
    rose on net **de-risking / covering**, not fresh leveraged longs.
  - **Verdict:** the bridge works and adds information CVD alone can't. This is
    exactly the froth-vs-squeeze distinction the pump-fade edge needs.

- `03_oi_regime_forward` — **first predictive test of the OI-regime; REFUTED on
  this data.** Does the froth (OI↑) vs squeeze (OI↓) split at the start of a move
  predict continuation vs reversal? Decision points every 1 min, pooled 5 syms
  (n=3732), outcome = **move-relative** forward return (`fwd × sign(Δprice)`,
  >0 = continued) at 30s/1m/5m, vs baseline. **Findings:**
  - **No edge.** froth/squeeze `edge_bp` vs baseline = ±0.01 bp at 30s/1m (zero);
    at 5m they split ~0.34 bp but the *wrong* way (froth reverses more) — all two
    orders below the ~1–2 bp spread floor. See `_out/03_oi_regime_forward.png`.
  - **Per-symbol signs disagree** (§4b: @5m SOL −1.28 vs SUI +1.09) → noise, not a
    diluted signal. SUI alone carries the only positive pooled cells.
  - **Baseline structural fact:** short-horizon moves mildly **mean-revert** (5m
    cont% 47%, −0.51 bp) regardless of OI — the market fades its own 1-min move.
  - **Crumb (sub-cost, §4a):** the raw 2×2 isn't move-symmetric; it collapses to
    "ΔOI↑ this minute → price down next minute, ΔOI↓ → up" *independent of move
    direction* (≤0.4 bp, one session, per-symbol stability untested). Move-relative
    folding cancelled it because it keys on OI sign, not move direction.
  - **Verdict:** the froth/squeeze edge is a **pump-regime** phenomenon; a calm
    session can't adjudicate it (nb02 said as much). Two honest next steps:
    (1) condition on real MOVES (large trailing |ret|), not every minute; (2)
    `live --record` a real pump and re-run. No edge to model yet.

- `04_force_vs_barrier_lab` — **user's force-vs-barrier idea on a real pump;
  PARTIAL support.** LAB pump (`2026-07-08T19-06-02Z`, +23.4%/2.9h, 273k trades).
  force = buy taker vol/sec (trailing 5s), barrier = ask depth above (near +15bps
  band vs all top-25), label = forward mid return @5s, n=10.4k @1s. **Findings:**
  - **Cost floor OK:** LAB spread median 7.7 bp; 5s moves median 22 bp (~3× floor).
  - **Absorption is real & correctly signed (§3):** at fixed buy-force, thin
    near-wall → +3 bp / thick → −3 bp forward, monotone across the weak-to-mid
    force rows (Q1–Q3); isolated thin−thick effect **+2.98 bp**. See
    `_out/04_force_barrier_surface.png` (green-left→red-right in bottom 3 rows).
  - **Inverts under heavy force (Q5):** strongest buying + thickest wall = +2.2 bp
    (breaks through) — reflexivity/bait, wall only absorbs when force isn't
    overwhelming.
  - **Near-band ≫ 'all supply above'** (+2.98 vs +1.11 bp): near-touch supply is
    informative, deep resting size mostly noise/spoof. (Answers the user's
    'sum everything above' concept — the wide sum is the *weaker* measure.)
  - **The single-ratio fusion `sec_to_eat=wall/flow` FAILS** — non-monotone,
    P(breakout)≈P(bounce). Effect lives in the 2D interaction; collapsing kills it.
  - **Verdict:** genuine signal but ~spread-sized (marginal standalone),
    force-regime-conditional, near-band only, needs the 2D surface not a ratio.
    Caveats: 1 pump/1 symbol, 250ms book = net depth only (no add/cancel churn),
    overlapping 1s samples (autocorr, subsample owed). Next: MAGMA+subsample
    robustness; add the *dynamics* (net wall-change, flow deceleration) the static
    snapshot misses — likely where the real edge is.

- `05_seeing_the_book` — **the liquidity heatmap: making microstructure visible.**
  Motivation (user): the tape/book is too much data/sec to read as a table; we
  need to *see* it like an OHLCV chart to build intuition and form hypotheses.
  New `_lab.micro_view(session, sym, t0, t1)` renders a recorded window as a
  trader sees order flow: order-book liquidity heatmap (x=time, y=price,
  colour=resting size; bids blue below / asks warm above, log-scaled), white mid
  line, trade bubbles (green buy / red sell, size∝amount, auto-capped ~3000 so it
  isn't mush), and CVD + force (taker vol/s) panels. Replay-of-recording (not
  live) so it's zoomable and *both user and Claude read the same PNG*.
  - First render = the LAB pump (`_out/05_lab_full.png`). Immediately visible:
    the ~3100s launch (CVD steps +450k), first peak ~1.49 @3300-4200s on the
    highest CVD (~600k), then the **final push to 1.50 near the end happened on
    LOWER CVD (~400k)** than the first peak — a CVD/price divergence worth a look.
  - **Data limit made concrete:** our book is only **top-25 levels**, so the
    heatmap is a thin *ribbon hugging price*, NOT a full-depth Bookmap — far/static
    walls were never captured. To watch true walls we'd need a full-depth
    (book-delta) capture. For now the ribbon shows near-touch thickening/thinning.

- `06_cvd_leads_price` — **hypothesis C (CVD tops before price): NOT confirmed.**
  The nb05 heatmap made LAB's top look like the spot where CVD stopped rising
  before price faded. Split honestly: descriptive lagged cross-correlation
  `corr(ΔCVD_t, Δprice_{t+k})` (no peak-picking) + a causal bearish-divergence →
  forward-return test. LAB + MAGMA, 5s grid. **Findings:**
  - **§1: CVD does NOT lead price.** xcorr is a pure **k=0 spike** (LAB +0.52,
    MAGMA +0.40), flat elsewhere; no k>0 shoulder. If anything price weakly leads
    CVD (k<0 side higher). The k=0 spike = the taker→price tautology.
    `_out/06_xcorr.png`.
  - **§2: divergence effect is LAB-only.** Pooled, divergence = weaker
    continuation than confirmed @120s (conf +6.64 vs div +2.14 bp, right
    direction) but still positive (not a reversal), and the sign flips @30s.
    Per-symbol: **LAB gap −10.76 bp (correct sign) but MAGMA +0.24 (nothing)** —
    one episode carries the whole pool.
  - **Verdict:** C *unconfirmed, leaning refuted*. Teaching moment — the heatmap
    gave a compelling, true-on-LAB story that (a) isn't a systematic lead even
    within LAB (xcorr) and (b) doesn't replicate on the 2nd pump. n=1 is an
    anecdote; see→hypothesize→TEST stopped us believing a chart. Re-run on the
    depth-200 pump harvest (n≫2) to adjudicate.

- `07_cvd_divergence_harvest` — **hypothesis C re-run on n=6 pumps: REFUTED.**
  The depth-200 harvest (`2026-07-10T11-31-38Z`) caught **6 real alt pumps** in
  2.9h (EVAA +28.9%, VELVET +18.4%, US +18.1%, SKL +16.4%, LAB +9.4%, TAC +8.9%),
  so C finally got an n≫2 test. Same nb06 machinery. **Findings:**
  - §1 xcorr pooled over 6 pumps = pure **k=0 spike (+0.497)**, flat elsewhere →
    CVD does NOT lead price (robust now, not just n=2). `_out/07_xcorr.png`.
  - §2a pooled looks supportive (divergence f120 −11.7 vs confirmed −6.9 bp) but
    **§2b per-symbol scatters: only 2/6 show the hypothesis sign** (EVAA −22.7,
    VELVET −21.6 — the two biggest pumps, dominating the pool); the other 4 (US,
    SKL, LAB, TAC) lean the OTHER way. This session's LAB gap +5.6 even flips
    nb06's −10.8 (different LAB episode).
  - **Verdict:** C dead as a general signal. Across 8 symbols / 2 sessions the
    directional CVD divergence→reversal refuses to replicate — same 'one/two
    episodes carry the pool' failure as nb03/nb06. Noted-but-unchased: the 2 that
    worked were the most violent pumps ('only bites in extreme moves' — too thin
    to chase, would be fishing).

## Live captures (pump harvest for the real tests)

- `2026-07-10T11-31-38Z` — **depth-200** headless capture, 8 syms (BTC, SOL +
  movers LAB/SKL/TAC/EVAA/US/VELVET), 4 streams, launched ~11:31Z for 6h. The
  first depth-25 capture (`...T11-22-49Z`, ~9 min) was superseded — depth 200
  stores a *wide* book (LAB span ~3380 bps) so the heatmap becomes a true
  full-depth Bookmap and real walls are captured. This is the sample nb04/nb06
  need re-running on. NOTE: launched from the agent session — for a multi-day
  harvest the user should run the collector in their own terminal (survives
  session end).

## Tooling

- **Realtime polygon** `microstructure.live` (built 2026-07-10; docs in
  `../README.md`): live Textual dashboard of the tape / CVD / OI-regime /
  funding / premium / spread / book-imbalance, extensible via `live/metrics.py`.
  Use it to build intuition on many live regimes (one recorded session is thin)
  and to `--record` a real pump for the bridge test below.

## Open threads / next steps

- **nb 01 (proposed):** core derived series from the book — spread (bps), top-N
  **order-book imbalance** `(bid_sz-ask_sz)/(bid_sz+ask_sz)`, short-horizon mid
  returns — and a first look at whether imbalance *leads* price on a
  seconds horizon (the classic first microstructure question). Baseline: shuffle
  the imbalance vs forward return; mind that BBO spread (~1-2 bp on BTC) is the
  cost floor for anything this fast.
- Liquidation `amount` is `None` on Bybit's linear feed — note for later; size
  may need reconstructing from something else if we want liq magnitude.
- Only one day / 5 symbols so far. Any shape found here needs a second capture
  (different day/regime) before we trust it.
