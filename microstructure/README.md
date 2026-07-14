# microstructure

Research area for short-horizon (seconds-to-minutes, scalping-style) strategies
driven by **live market-microstructure data** from Bybit — order book, trade
tape, ticker/BBO + funding/OI, and liquidations. Not HFT (yet): we sample the
book rather than log every delta.

This is a manual-research area in the spirit of `notebooks/` — slow, honest,
journal-as-you-go. This README is the running journal.

---

## The collector

`microstructure.collector` captures live streams over one ccxt.pro WebSocket
connection and rolls them to zstd parquet, organised per capture *session*.

### Run it

```bash
# interactive Textual dashboard (default) — runs until you press q
uv run python -m microstructure.collector --symbols BTCUSDT,ETHUSDT

# headless, capture 4 streams for 2 hours, then flush & exit
uv run python -m microstructure.collector \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT \
    --streams book,trades,ticker,liq \
    --no-ui --duration 7200
```

Flags: `--depth` (book levels stored per side, default 25), `--sample-ms`
(book snapshot cadence, default 250), `--flush-s` (parquet part rotation,
default 60), `--session-id` (override dir name).

### Dashboard keys

| key | action |
|-----|--------|
| `a` | add a symbol (prompt) |
| `p` | pause / resume the highlighted symbol (keeps feed warm, stops writing) |
| `d` | remove the highlighted symbol (stops + flushes it) |
| `q` | quit — flushes every buffer before exit |

### Streams & storage

| stream | ccxt.pro method | notes |
|--------|-----------------|-------|
| `orderbook` | `watch_order_book` | subscribe at nearest valid Bybit depth ∈ {1,50,200,1000}, store top-N *wide* (`bid_px_i/bid_sz_i/ask_px_i/ask_sz_i`), sampled every `--sample-ms` |
| `trades` | `watch_trades` | every public trade: `price, amount, side, id` |
| `ticker` | `watch_ticker` | `last, bid, ask, mark, index, funding_rate, open_interest, volumes` |
| `liquidations` | `watch_liquidations` | `side, price, amount` (sparse — often silent for minutes) |

Every row carries `ts_exchange` (event time) and `ts_local` (receipt) in ms, so
feed lag is `ts_local - ts_exchange` (typically ~0.5s here).

On disk (honours `$RESEARCHLAB_DATA_ROOT`, same as `datafeed`):

```
data/bybit/micro/sessions/<session_id>/
    manifest.json                         # config, symbols, start/end, totals
    orderbook/BTCUSDT/part-00001.parquet  # rolling parts, one per flush
    trades/BTCUSDT/part-00001.parquet
    ticker/… liquidations/…
```

Rolling separate parts (not one growing file) keeps captures crash-safe: a hard
kill loses at most the current in-memory buffer; every written part is complete.

### Read it back

```python
from microstructure import loader
loader.list_sessions()                     # -> ['2026-07-08T18-…Z', …]
ob = loader.load(loader.latest_session(), "orderbook", "BTCUSDT", with_datetime=True)
mid = (ob["bid_px_0"] + ob["ask_px_0"]) / 2
tr  = loader.load(loader.latest_session(), "trades", "BTCUSDT")
```

---

## Design decisions (2026-07-08)

- **Sampled book, not full delta log.** Target horizon is seconds-to-minutes.
  A 250 ms top-25 snapshot is plenty and an order of magnitude smaller than a
  full incremental log. Revisit if a strategy needs sub-sample dynamics.
- **Wide parquet + zstd.** Same stack as `datafeed/`. Rectangular book frames
  read straight into strategy code; correlated price/size columns compress well.
- **One shared ccxt.pro client** multiplexes all symbols/streams over one WS.
- **Parquet encode off the event loop** (`asyncio.to_thread`) so flushes never
  stall the feeds.

## The realtime polygon (`microstructure.live`)

A **live viewer** for order flow — the collector's sibling. It subscribes to the
same Bybit streams (reusing `collector.streams` watch loops verbatim) but,
instead of writing parquet, computes rolling microstructure metrics per symbol
and renders them in a Textual dashboard. Built to be *extended*: each metric is
one small function in `live/metrics.py` and the UI grows a column automatically.

```bash
# live dashboard, 60s rolling window
uv run python -m microstructure.live --symbols BTCUSDT,ETHUSDT

# faster window + record to parquet while watching (lands like a collector session)
uv run python -m microstructure.live --symbols BTCUSDT --window 30 --record

# headless snapshot printer (no TUI) — good for logging / a fixed run
uv run python -m microstructure.live --symbols BTCUSDT --no-ui --duration 30
```

Metrics shown (v0): price, spread (bps), top-5 book **imbalance**, **CVD**
(rolling window + session), taker **buy%**, trades/s, **OI** + **ΔOI**, the
**price×OI regime** label (new longs / short cover / new shorts / long exit),
**funding** (bps, + countdown to the next 8h stamp) and **premium** = (mark −
index) in bps, and a windowed liquidation count. The highlighted symbol gets a
detail panel + a live colour-coded trade tape. Keys: `a` add · `d` remove · `q`
quit.

### Visual view (`--qt`)

A Bookmap-style PyQtGraph renderer (`live/qt_ui.py`) is the visual sibling of the
Textual table — same `Tap → SymbolState` seam, drawn instead of tabulated. A
**heatmap** (x=time, y=price, colour=resting size: asks red / bids blue) carries
the white mid line, green/red trade bubbles and liquidation marks; below it sit
**CVD**, **force** (taker vol/s, buy up / sell down) and **OI** strip plots. It
runs in the one qasync asyncio loop (no threads). The heatmap's price band is
**fit per symbol from that symbol's own captured book** (sized so the bulk of the
stored depth spans the rows), so majors with a dense fine-tick book (BTC) show
real vertical structure instead of a thin line while coarse-tick alts still fill
the band. The fitted bin size is fixed once and the grid scrolls **vertically** as
the mid drifts — liquidity history is kept at each absolute price rather than
wiped, so the map and mid line cover the same time span even on a trending/pumping
symbol.

```bash
# visual view: 180s window, focus BTC, full-depth book auto-selected
uv run python -m microstructure.live --symbols BTCUSDT,SOLUSDT --qt --focus BTCUSDT

# narrower window + periodic PNG snapshot (also lets a headless run be verified)
uv run python -m microstructure.live --symbols BTCUSDT --qt --view-seconds 120 \
    --duration 60 --snapshot view.png --snapshot-every 15
```

Keys: `f` toggle **follow** (auto-scroll/-range; mouse zoom or pan drops out of
follow until pressed again) · `c` **centre/reset** the view (re-frame the focused
symbol on its own price scale + re-enter follow) · `space` **freeze** the display
(data keeps flowing) · `1`..`9` focus a symbol (each keeps its own accumulated
buffers; switching auto-frames the new symbol, so assets on wildly different
price scales — e.g. BTC vs an alt — frame correctly) · `t` toggle the **tape**
between raw bubbles and a **clustered footprint** (one marker per time×price
cell, size ∝ volume, colour by net delta; cells whose |net delta| clears the
threshold drop **persistent horizontal level markers** that stay drawn as the
view scrolls, so you can eyeball whether price later reacts there) · `s`
snapshot. Mouse hover shows a **crosshair** with the time / price (and resting
size) under the cursor. A compact **control row** tunes, live and without a
restart: band × (a multiplier on the per-symbol adaptive band — widen/tighten it),
contrast percentile, view-seconds, and the imbalance-marker threshold (0 disables
the markers). Flags: `--focus <sym>`,
`--view-seconds <s>` (default 180), `--snapshot <path>` / `--snapshot-every <s>`.
Closing the window shuts the engine down cleanly.

Design: `live/state.py` (`SymbolState` — rolling deques + incremental metrics),
`live/metrics.py` (the extension registry), `live/engine.py` (`Tap` shim →
reuses `RUNNERS`; optional `Sink` for `--record`), `live/ui.py` (Textual),
`live/__main__.py` (CLI). The `Tap` is the key seam: it's shaped like the
collector's `Sink` (`append`/`append_many`) so the watch loops don't know or
care they're feeding a live view instead of disk.

## Journal

- **2026-07-08 — collector built & smoke-tested.** BTCUSDT/ETHUSDT live capture
  verified: orderbook (after snapping depth to Bybit's {1,50,200,1000} rule),
  trades, ticker (funding/OI present) all writing; TUI add/pause/remove/quit and
  loader round-trip all green. Liquidations wired but not yet observed firing.
  Feed lag ~0.58s. No strategy work yet — next step is to capture a couple of
  hours across a small symbol set and start EDA on order-flow imbalance / book
  pressure vs short-horizon returns.
- **2026-07-10 — EDA line `initial/` + realtime polygon built.** Opened the
  14.3h 2026-07-09 capture (`initial/00_open_data`): data trustworthy (book
  ~260ms, ~1544 trades/min, feed lag ~0.28s). Built **CVD** from the tape
  (`01_cvd_btc`, contemporaneous corr with price +0.46) and the **CVD↔OI bridge**
  (`02_cvd_oi_bridge`): the 2×2 price×OI split cleanly separates *new longs*
  from *short-covering* — the ~16:00→18:00 rally was a squeeze (CVD +900 but OI
  collapsed 56.0k→55.0k), the midday pop was fresh longs. Then built the **live
  polygon** (`microstructure.live`, see above) reusing the collector's watch
  loops via a `Tap` shim; verified live against Bybit (all metrics), `--record`
  round-trips through `loader`. Next: lead/lag on CVD, and re-run the bridge on
  a real pump episode (capture one live with `--record`).
