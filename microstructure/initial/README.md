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
