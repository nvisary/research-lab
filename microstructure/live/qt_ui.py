"""PyQtGraph live view for the realtime microstructure polygon.

A *visual* consumer of the same `engine.Tap -> SymbolState` seam the Textual TUI
uses — but instead of a table of numbers it renders order flow the way a trader
*sees* it:

    ┌ readout: price · spread · imbalance · funding(+countdown) · premium · regime ┐
    │ HEATMAP  — x=time, y=price, colour=resting size (bids blue / asks red),      │
    │            white mid line, trade bubbles (green buy / red sell), liq marks   │
    ├──────────────────────────────────────────────────────────────────────────────┤
    │ CVD   (cumulative signed taker volume)                                        │
    │ FORCE (taker volume/sec, buy up / sell down)                                  │
    │ OI    (open interest)                                                         │
    └────────────────────────────────────────────────────────────────────────────┘

Design: everything runs in ONE asyncio loop via ``qasync`` (Qt event loop hosts
asyncio), honouring ``state.py``'s lock-free single-loop assumption. The engine's
ccxt.pro watch loops fill ``SymbolState``; a ~2 Hz refresh reads that state and
updates the widgets. The heatmap keeps its OWN rolling column buffer here (the
shared state only holds the latest book), sampled at the refresh cadence, and it
scrolls VERTICALLY in price — liquidity history is kept at each *absolute* price
rather than wiped whenever the mid drifts.

Interaction: the view *follows* live by default; zoom/pan with the mouse drops
out of follow (press ``f`` to re-engage). ``c`` re-frames the focused symbol on
its own price scale and re-enters follow (also done automatically on every
symbol switch, so assets on wildly different price scales frame correctly).
``space`` freezes the display (data keeps flowing), ``1``..``9`` switch the
focused symbol (each keeps its own accumulated buffers), ``t`` toggles the tape
between raw bubbles and a clustered footprint (with persistent large-imbalance
level markers), and ``s`` dumps a PNG snapshot — which also lets a headless run
be verified. A compact control row tunes band %, contrast percentile, view
window and the imbalance threshold live, without a restart.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from microstructure.live.engine import LiveEngine

pg.setConfigOptions(antialias=False, background="#0b0b12", foreground="#9aa")

REFRESH_S = 0.5          # UI refresh / heatmap column cadence
NY = 260                 # heatmap price bins (rows)
BAND_PCT = 0.02          # fallback half-band (fraction of mid) when the book is
                         # too thin to fit adaptively
ADAPT_PCTL = 0.92        # band fits this percentile of |level_px - mid| (ignores
                         # far outliers) so the bulk of the book fills the rows
ADAPT_HEADROOM = 1.25    # widen the fitted band a touch for mid-drift margin
ADAPT_MIN_LEVELS = 8     # need at least this many priced levels to trust a fit
ADAPT_MAX_WAIT = 6       # ticks to wait for a fuller book before falling back
TAPE_MAX = 400           # max trade bubbles drawn
DEFAULT_VIEW_S = 180.0   # default visible time window (seconds)
DEFAULT_CONTRAST = 99.0  # heatmap level = this percentile of |grid| (UI-tunable)
CLUSTER_NT = 90          # clustered-tape time cells across the window
CLUSTER_NP = 80          # clustered-tape price cells across the band
DEFAULT_IMBAL = 5.0      # persistent-marker threshold on |net delta| (base units)
IMBAL_CAP = 80           # max persistent imbalance markers kept


def _diverging_lut(n: int = 256) -> np.ndarray:
    """bids (negative) -> blue, empty (0) -> near-black, asks (positive) -> red."""
    cmap = pg.ColorMap(
        [0.0, 0.5, 1.0],
        [(60, 130, 255, 255), (11, 11, 18, 255), (255, 70, 90, 255)],
    )
    return cmap.getLookupTable(0.0, 1.0, n)


class HeatmapBuffer:
    """Rolling [NY price x NX time] grid of resting size; asks +, bids -.

    The price axis uses a *fixed* bin size (price per row). The half-band is fit
    ONCE per symbol from the symbol's actual captured book — sized so the bulk of
    the stored depth spans the rows — rather than a fixed % of price. This
    auto-tightens for majors whose dense fine-tick book sits within a hair of mid
    (BTC) and widens for coarse-tick alts, so both show real vertical structure.
    A UI ``band_scale`` multiplies the fitted band (default 1.0). Time scrolls
    left (``np.roll`` axis 1) one column per push. When the mid nears a band edge
    the whole grid scrolls VERTICALLY (``np.roll`` axis 0) by an integer number
    of rows, zeroing only the newly-exposed rows — so the liquidity recorded at
    each absolute price survives a trending mid instead of being wiped.
    """

    def __init__(self, ny: int = NY, nx: int = 600, band_scale: float = 1.0) -> None:
        self.ny, self.nx = ny, nx
        self.band_scale = band_scale
        self.grid = np.zeros((ny, nx), dtype=np.float32)
        self.bin: float | None = None      # price per row (fixed once set)
        self.lo = self.hi = None           # price of row-0 bottom / row-ny top
        self._miss = 0                     # ticks waited for a fittable book

    @staticmethod
    def _fit_half(book: dict | None, mid: float) -> float | None:
        """Half-band (absolute price) that covers ADAPT_PCTL of the priced
        levels' distance from mid. None if the book is too thin to trust."""
        if not book:
            return None
        ds = []
        for side in ("ask", "bid"):
            i = 0
            while True:
                px = book.get(f"{side}_px_{i}")
                if px is None:
                    break
                if book.get(f"{side}_sz_{i}") or 0.0:
                    ds.append(abs(px - mid))
                i += 1
                if i > 500:
                    break
        if len(ds) < ADAPT_MIN_LEVELS:
            return None
        ds.sort()
        p = ds[min(len(ds) - 1, int(ADAPT_PCTL * len(ds)))]
        half = p * ADAPT_HEADROOM
        return half if half > 0 else None

    def _set_range(self, mid: float, half: float) -> None:
        self.bin = (2.0 * half) / self.ny
        self.lo = mid - half
        self.hi = mid + half

    def _recenter(self, mid: float) -> None:
        """Scroll the grid vertically so ``mid`` is centred again, preserving
        the size history at every absolute price."""
        target_lo = mid - (self.ny / 2.0) * self.bin
        shift = int(round((target_lo - self.lo) / self.bin))
        if shift == 0:
            return
        if abs(shift) >= self.ny:
            self.grid[:] = 0.0
        else:
            # lo increases -> a given price maps to a lower row -> data moves
            # toward lower indices (roll by -shift); newly exposed rows zeroed.
            self.grid = np.roll(self.grid, -shift, axis=0)
            if shift > 0:
                self.grid[self.ny - shift:, :] = 0.0
            else:
                self.grid[:-shift, :] = 0.0
        self.lo += shift * self.bin
        self.hi = self.lo + self.ny * self.bin

    def push(self, book: dict | None, mid: float | None) -> None:
        if mid is None:
            return
        if self.lo is None:
            # fit the band from the actual book; wait briefly for a fuller book
            # before falling back to the fixed % so the fit is representative.
            half = self._fit_half(book, mid)
            if half is None:
                self._miss += 1
                if self._miss < ADAPT_MAX_WAIT:
                    return
                half = mid * BAND_PCT      # fallback: thin/absent book
            self._set_range(mid, half * self.band_scale)
        else:
            margin = 0.15 * (self.hi - self.lo)
            if mid < self.lo + margin or mid > self.hi - margin:
                self._recenter(mid)
        col = np.zeros(self.ny, dtype=np.float32)
        if book:
            for side, sgn in (("ask", 1.0), ("bid", -1.0)):
                i = 0
                while True:
                    px = book.get(f"{side}_px_{i}")
                    if px is None:
                        break
                    sz = book.get(f"{side}_sz_{i}") or 0.0
                    if sz and self.lo <= px < self.hi:
                        r = min(int((px - self.lo) / self.bin), self.ny - 1)
                        col[r] += sgn * np.log1p(sz)
                    i += 1
                    if i > 500:
                        break
        self.grid = np.roll(self.grid, -1, axis=1)
        self.grid[:, -1] = col

    def levels(self, pct: float = DEFAULT_CONTRAST) -> float:
        """Contrast level from a high percentile of |grid| so a lone whale
        order doesn't wash the whole map out."""
        a = np.abs(self.grid)
        nz = a[a > 0]
        if nz.size == 0:
            return 1.0
        L = float(np.percentile(nz, pct))
        return L if L > 0 else 1.0

    def cell(self, price: float, frac: float) -> float | None:
        """Signed size at a price and time-fraction (0=oldest col, 1=newest)."""
        if self.lo is None or self.bin is None:
            return None
        r = int((price - self.lo) / self.bin)
        c = int(round(frac * (self.nx - 1)))
        if 0 <= r < self.ny and 0 <= c < self.nx:
            return float(self.grid[r, c])
        return None


class SymBuf:
    """Per-symbol accumulated view: heatmap + the rolling line series."""

    def __init__(self, nx: int, band_scale: float = 1.0) -> None:
        self.hb = HeatmapBuffer(NY, nx, band_scale)
        self.mid: list[tuple[float, float]] = []
        self.cvd: list[tuple[float, float]] = []
        self.buy: list[tuple[float, float]] = []
        self.sell: list[tuple[float, float]] = []
        self.oi: list[tuple[float, float]] = []


class MicroWindow(QtWidgets.QMainWindow):
    def __init__(self, engine: LiveEngine, symbol: str | None = None,
                 view_seconds: float = DEFAULT_VIEW_S) -> None:
        super().__init__()
        self.engine = engine
        self.symbol = symbol
        self.view_seconds = view_seconds
        self.nx = max(2, int(round(view_seconds / REFRESH_S)))
        self.t0_ms = time.time() * 1000.0
        self._lut = _diverging_lut()
        self.bufs: dict[str, SymBuf] = {}

        # interaction state
        self.following = True
        self.paused = False
        self._applying = False        # guard: programmatic range set (not manual)
        self._now = 0.0
        self._tmin = 0.0

        # tunable settings (also driven live by the control row, below)
        self.band_scale = 1.0         # multiplier on the per-symbol adaptive band
        self.contrast_pct = DEFAULT_CONTRAST
        self.imbal_thresh = DEFAULT_IMBAL
        self.clustered = False        # tape mode: raw bubbles vs clustered footprint
        # per-symbol persistent large-imbalance markers: key -> {price,t0,delta}
        self._imarks: dict[str, dict] = {}

        # shutdown plumbing (set by run_qt / _driver)
        self._driver_task: asyncio.Task | None = None
        self._shutdown_done = False
        self._snapshot_path: str | None = None

        self.resize(1500, 950)
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central); v.setContentsMargins(4, 2, 4, 2); v.setSpacing(2)

        self.readout = QtWidgets.QLabel("starting…")
        self.readout.setStyleSheet("color:#ddd; font-family:Consolas,monospace; font-size:12px;")
        v.addWidget(self.readout)

        v.addWidget(self._build_controls())

        gl = pg.GraphicsLayoutWidget(); v.addWidget(gl, 1)
        self.gl = gl
        self.p_heat = gl.addPlot(row=0, col=0)
        self.p_cvd = gl.addPlot(row=1, col=0)
        self.p_force = gl.addPlot(row=2, col=0)
        self.p_oi = gl.addPlot(row=3, col=0)
        gl.ci.layout.setRowStretchFactor(0, 6)
        for p in (self.p_cvd, self.p_force, self.p_oi):
            p.setXLink(self.p_heat)
            p.showGrid(x=False, y=True, alpha=0.15)
            p.enableAutoRange(x=False, y=True)   # x driven by follow; y auto
        self.p_heat.showGrid(x=True, y=True, alpha=0.12)
        self.p_heat.getViewBox().disableAutoRange()
        self.p_cvd.setLabel("left", "CVD")
        self.p_force.setLabel("left", "force/s")
        self.p_oi.setLabel("left", "OI")
        self.p_oi.setLabel("bottom", "seconds")

        # mouse zoom/pan on every plot; pan-drag feels natural for a chart
        for p in (self.p_heat, self.p_cvd, self.p_force, self.p_oi):
            vb = p.getViewBox()
            vb.setMouseMode(pg.ViewBox.PanMode)
            vb.sigRangeChangedManually.connect(self._on_manual_range)

        self.img = pg.ImageItem()
        self.img.setLookupTable(self._lut)
        self.p_heat.addItem(self.img)
        self.mid_curve = self.p_heat.plot([], [], pen=pg.mkPen("#ffffff", width=1.2))
        self.tape = pg.ScatterPlotItem(pxMode=True, size=6)
        self.p_heat.addItem(self.tape)
        self.liq = pg.ScatterPlotItem(pxMode=True, size=13, symbol="x",
                                      pen=pg.mkPen("#ffd24a", width=2))
        self.p_heat.addItem(self.liq)
        # persistent large-imbalance level markers (clustered mode) — segments
        # from the event time forward to now; buy net green, sell net red.
        self.imbal_buy = pg.PlotCurveItem(pen=pg.mkPen((37, 255, 154, 150), width=2),
                                          connect="finite")
        self.imbal_sell = pg.PlotCurveItem(pen=pg.mkPen((255, 77, 94, 150), width=2),
                                           connect="finite")
        self.p_heat.addItem(self.imbal_buy)
        self.p_heat.addItem(self.imbal_sell)
        self.cvd_curve = self.p_cvd.plot([], [], pen=pg.mkPen("#f0c000", width=1.2))
        self.buy_curve = self.p_force.plot([], [], fillLevel=0,
                                           brush=(37, 255, 154, 90), pen=pg.mkPen("#25ff9a"))
        self.sell_curve = self.p_force.plot([], [], fillLevel=0,
                                            brush=(255, 77, 94, 90), pen=pg.mkPen("#ff4d5e"))
        self.oi_curve = self.p_oi.plot([], [], pen=pg.mkPen("#8e6bff", width=1.2))

        # crosshair over the heatmap
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#556", width=1))
        self.hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#556", width=1))
        self.p_heat.addItem(self.vline, ignoreBounds=True)
        self.p_heat.addItem(self.hline, ignoreBounds=True)
        self.xhair_label = pg.TextItem(color="#cde", anchor=(0, 1))
        self.xhair_label.setZValue(100)
        self.p_heat.addItem(self.xhair_label, ignoreBounds=True)
        for it in (self.vline, self.hline, self.xhair_label):
            it.setVisible(False)   # shown on first hover
        self._mouse_proxy = pg.SignalProxy(gl.scene().sigMouseMoved,
                                           rateLimit=60, slot=self._on_mouse)

        self._update_title()

    # ---- helpers ---------------------------------------------------------
    def _focus(self) -> str | None:
        if self.symbol and self.symbol in self.engine.states:
            return self.symbol
        return self.engine.symbols[0] if self.engine.symbols else None

    def _buf(self, sym: str) -> SymBuf:
        buf = self.bufs.get(sym)
        if buf is None:
            buf = SymBuf(self.nx, self.band_scale)
            self.bufs[sym] = buf
        return buf

    def _tsec(self, ms: float) -> float:
        return (ms - self.t0_ms) / 1000.0

    @staticmethod
    def _trim(series: list, tmin: float) -> None:
        while series and series[0][0] < tmin:
            series.pop(0)

    def _update_title(self) -> None:
        sym = self._focus() or "—"
        mode = "FOLLOW" if self.following else "FREE"
        if self.paused:
            mode += " · PAUSED"
        self.setWindowTitle(f"microstructure — {sym}   [{mode}]   {self.view_seconds:.0f}s")

    def _on_manual_range(self, *_args) -> None:
        # user zoomed/panned with the mouse -> stop fighting them
        if self._applying:
            return
        if self.following:
            self.following = False
            self._update_title()

    # ---- live settings controls -----------------------------------------
    def _build_controls(self) -> QtWidgets.QWidget:
        """A compact control row: band × (multiplier on the per-symbol adaptive
        band), contrast percentile, view window, and the persistent-imbalance
        threshold — all adjustable without a restart. Band / view-seconds rebuild
        the per-symbol buffers."""
        ctrl = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(ctrl)
        h.setContentsMargins(2, 0, 2, 0); h.setSpacing(6)

        def spin(lo, hi, step, val, dec, width=78):
            sb = QtWidgets.QDoubleSpinBox()
            sb.setRange(lo, hi); sb.setSingleStep(step); sb.setDecimals(dec)
            sb.setValue(val); sb.setKeyboardTracking(False)
            sb.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
            sb.setMaximumWidth(width)
            return sb

        self.sb_band = spin(0.1, 10.0, 0.1, self.band_scale, 1)
        self.sb_contrast = spin(50.0, 100.0, 1.0, self.contrast_pct, 0, 66)
        self.sb_view = spin(20.0, 1800.0, 10.0, self.view_seconds, 0, 78)
        self.sb_imbal = spin(0.0, 1e9, 1.0, self.imbal_thresh, 2, 96)
        for lbl, sb in (("band ×", self.sb_band), ("contrast %ile", self.sb_contrast),
                        ("view s", self.sb_view), ("imbal Δ", self.sb_imbal)):
            tag = QtWidgets.QLabel(lbl)
            tag.setStyleSheet("color:#9aa; font-family:Consolas,monospace; font-size:11px;")
            h.addWidget(tag); h.addWidget(sb)
        h.addStretch(1)
        ctrl.setStyleSheet(
            "QDoubleSpinBox{background:#15151f;color:#ddd;border:1px solid #333;"
            "font-family:Consolas,monospace;font-size:11px;}")

        self.sb_band.valueChanged.connect(self._on_band_changed)
        self.sb_contrast.valueChanged.connect(self._on_contrast_changed)
        self.sb_view.valueChanged.connect(self._on_view_changed)
        self.sb_imbal.valueChanged.connect(self._on_imbal_changed)
        return ctrl

    def _rebuild_buffers(self) -> None:
        """Drop the per-symbol buffers so they rebuild with the current band /
        view-seconds (a brief reset — accepted for a live settings change)."""
        self.nx = max(2, int(round(self.view_seconds / REFRESH_S)))
        self.bufs.clear()
        self._imarks.clear()

    def _on_band_changed(self, val: float) -> None:
        self.band_scale = float(val)
        self._rebuild_buffers()

    def _on_view_changed(self, val: float) -> None:
        self.view_seconds = float(val)
        self._rebuild_buffers()
        self._update_title()

    def _on_contrast_changed(self, val: float) -> None:
        self.contrast_pct = float(val)

    def _on_imbal_changed(self, val: float) -> None:
        self.imbal_thresh = float(val)

    # ---- centre / reset --------------------------------------------------
    def _center_view(self) -> None:
        """Re-frame the focused symbol on its own price scale and live window,
        and re-enter follow — used by the `c` key and on every symbol switch."""
        self.following = True
        self._update_title()
        self._redraw()

    # ---- clustered tape + persistent imbalance markers ------------------
    def _cluster_spots(self, sym: str, s, buf: SymBuf, tmin: float, now: float) -> list:
        """Aggregate window trades into (time-bin x price-bin) cells: one marker
        per cell, size ∝ total volume, colour by net delta (green buy / red
        sell). Cells whose |net delta| clears the threshold register a
        persistent horizontal level marker."""
        lo, hi = buf.hb.lo, buf.hb.hi
        if lo is None:
            return []
        span_t = now - tmin
        dt = span_t / CLUSTER_NT if span_t > 0 else 1.0
        dp = (hi - lo) / CLUSTER_NP
        if dp <= 0 or dt <= 0:
            return []
        cells: dict[tuple[int, int], list[float]] = {}
        for (ts, side, amt, px, _sg) in s.trades:
            if px is None:
                continue
            x = self._tsec(ts)
            if x < tmin:
                continue
            pi = int((px - lo) / dp)
            if not (0 <= pi < CLUSTER_NP):
                continue
            ti = int((x - tmin) / dt)
            c = cells.setdefault((ti, pi), [0.0, 0.0])
            c[0] += amt
            c[1] += amt if side == "buy" else -amt
        marks = self._imarks.setdefault(sym, {})
        spots = []
        if cells:
            vmax = max(c[0] for c in cells.values()) or 1.0
            for (ti, pi), (vol, delta) in cells.items():
                x = tmin + (ti + 0.5) * dt
                y = lo + (pi + 0.5) * dp
                net_buy = delta >= 0
                spots.append(dict(pos=(x, y), size=5 + 20 * (vol / vmax),
                                  brush=pg.mkBrush(37, 255, 154, 175) if net_buy
                                  else pg.mkBrush(255, 77, 94, 175),
                                  pen=None))
                if self.imbal_thresh > 0 and abs(delta) >= self.imbal_thresh:
                    # key on absolute price/time buckets so the same real
                    # concentration maps to one stable, non-duplicating mark.
                    key = (round(y / dp), round(x / dt))
                    if key not in marks:
                        marks[key] = {"price": y, "t0": x, "delta": delta}
        self._evict_marks(sym, tmin)
        return spots

    def _evict_marks(self, sym: str, tmin: float) -> None:
        marks = self._imarks.get(sym)
        if not marks:
            return
        for k in [k for k, m in marks.items() if m["t0"] < tmin]:
            del marks[k]
        if len(marks) > IMBAL_CAP:   # keep the strongest |delta|
            drop = sorted(marks, key=lambda k: abs(marks[k]["delta"]))[:len(marks) - IMBAL_CAP]
            for k in drop:
                del marks[k]

    def _draw_imbalance(self, sym: str, now: float) -> None:
        marks = self._imarks.get(sym, {})
        bx: list[float] = []; by: list[float] = []
        sx: list[float] = []; sy: list[float] = []
        for m in marks.values():
            ax, ay = (bx, by) if m["delta"] >= 0 else (sx, sy)
            ax += [m["t0"], now, np.nan]      # segment from event time -> now
            ay += [m["price"], m["price"], np.nan]
        self.imbal_buy.setData(bx, by, connect="finite")
        self.imbal_sell.setData(sx, sy, connect="finite")

    # ---- the refresh -----------------------------------------------------
    def refresh(self) -> None:
        # 1) always ingest into EVERY symbol's buffers so switching focus keeps
        #    each one's accumulated view (data flows even when not shown).
        now = self._tsec(time.time() * 1000.0)
        tmin = now - self.nx * REFRESH_S
        for sym in self.engine.symbols:
            s = self.engine.states.get(sym)
            if s is None:
                continue
            buf = self._buf(sym)
            mid = s.mid()
            buf.hb.push(s.last_book, mid)
            if mid is not None:
                buf.mid.append((now, mid))
            buf.cvd.append((now, s.cvd_session()))
            buf.oi.append((now, s.oi() if s.oi() is not None else np.nan))
            s._evict()
            buy_v = sum(t[2] for t in s.trades if t[1] == "buy")
            sell_v = sum(t[2] for t in s.trades if t[1] == "sell")
            buf.buy.append((now, buy_v / s.window_s))
            buf.sell.append((now, -sell_v / s.window_s))
            for ser in (buf.mid, buf.cvd, buf.oi, buf.buy, buf.sell):
                self._trim(ser, tmin)
        self._now, self._tmin = now, tmin

        # 2) freeze the picture while paused (buffers above still advanced)
        if self.paused:
            return
        self._redraw()

    def _redraw(self) -> None:
        sym = self._focus()
        if sym is None:
            return
        s = self.engine.states.get(sym)
        if s is None:
            return
        buf = self._buf(sym)
        now, tmin = self._now, self._tmin

        # heatmap image + rect (columns map to [tmin, now] x [lo, hi])
        if buf.hb.lo is not None:
            L = buf.hb.levels(self.contrast_pct)
            self.img.setImage(buf.hb.grid.T, autoLevels=False, levels=(-L, L))
            self.img.setRect(QtCore.QRectF(tmin, buf.hb.lo, now - tmin,
                                           buf.hb.hi - buf.hb.lo))

        if buf.mid:
            mx, my = zip(*buf.mid); self.mid_curve.setData(mx, my)
        else:
            self.mid_curve.setData([], [])
        if buf.cvd:
            cx, cy = zip(*buf.cvd); self.cvd_curve.setData(cx, cy)
        if buf.buy:
            bx, by = zip(*buf.buy); self.buy_curve.setData(bx, by)
            sx, sy = zip(*buf.sell); self.sell_curve.setData(sx, sy)
        if buf.oi:
            ox, oy = zip(*buf.oi); self.oi_curve.setData(ox, oy)

        # tape: clustered footprint (+ persistent imbalance marks) or raw bubbles
        if self.clustered:
            self.tape.setData(self._cluster_spots(sym, s, buf, tmin, now))
            self._draw_imbalance(sym, now)
        else:
            self.imbal_buy.setData([], []); self.imbal_sell.setData([], [])
            trs = list(s.trades)[-TAPE_MAX:]
            if trs:
                spots = []
                amax = max(t[2] for t in trs) or 1.0
                for (ts, side, amt, px, _sg) in trs:
                    if px is None:
                        continue
                    spots.append(dict(pos=(self._tsec(ts), px),
                                      size=4 + 12 * (amt / amax),
                                      brush=pg.mkBrush(37, 255, 154, 150) if side == "buy"
                                      else pg.mkBrush(255, 77, 94, 150),
                                      pen=None))
                self.tape.setData(spots)
            else:
                self.tape.setData([])
        # liquidation marks
        lq = [(self._tsec(ts), px) for (ts, side, px) in s.liqs if px is not None]
        self.liq.setData([p[0] for p in lq], [p[1] for p in lq]) if lq else self.liq.setData([], [])

        # ranges — only while following, so mouse zoom/pan sticks otherwise
        if self.following:
            self._applying = True
            try:
                self.p_heat.setXRange(tmin, now, padding=0)
                if buf.hb.lo is not None:
                    self.p_heat.setYRange(buf.hb.lo, buf.hb.hi, padding=0)
            finally:
                self._applying = False

        self._update_readout(s, sym)
        if self._snapshot_path:
            self.snapshot(self._snapshot_path)

    def _on_mouse(self, evt) -> None:
        pos = evt[0]
        vb = self.p_heat.getViewBox()
        if not self.p_heat.sceneBoundingRect().contains(pos):
            return
        pt = vb.mapSceneToView(pos)
        x, y = pt.x(), pt.y()
        for it in (self.vline, self.hline, self.xhair_label):
            it.setVisible(True)
        self.vline.setPos(x)
        self.hline.setPos(y)
        clock = datetime.fromtimestamp(self.t0_ms / 1000.0 + x).strftime("%H:%M:%S")
        txt = f"{clock}   px {y:.4g}"
        buf = self.bufs.get(self._focus() or "")
        if buf is not None and self._now > self._tmin:
            frac = (x - self._tmin) / (self._now - self._tmin)
            v = buf.hb.cell(y, frac)
            if v is not None and v != 0.0:
                side = "ask" if v > 0 else "bid"
                txt += f"   {side}≈{np.expm1(abs(v)):.3g}"
        self.xhair_label.setText(txt)
        self.xhair_label.setPos(x, y)

    def _update_readout(self, s, sym: str) -> None:
        def f(v, spec="{:.4g}"):
            return spec.format(v) if v is not None else "-"
        fr = s.funding_rate()
        mode = "follow" if self.following else "free"
        if self.paused:
            mode += "·paused"
        mode += "·cluster" if self.clustered else ""
        parts = [
            f"● {sym}",
            f"px {f(s.price())}",
            f"spr {f(s.spread_bps(), '{:.1f}')}bp",
            f"imb5 {f(s.imbalance(5), '{:+.2f}')}",
            f"fund {f(1e4*fr if fr is not None else None, '{:+.2f}')}bp",
            f"prem {f(s.premium_bps(), '{:+.2f}')}bp",
            f"tr/s {f(s.trades_per_s(), '{:.1f}')}",
            f"regime {s.regime()}",
            f"[{mode}]",
        ]
        self.readout.setText("   ".join(parts))

    def snapshot(self, path: str) -> None:
        self.grab().save(path)

    # ---- keys ------------------------------------------------------------
    def keyPressEvent(self, ev) -> None:  # noqa: N802
        key = ev.key()
        txt = ev.text()
        if key == QtCore.Qt.Key.Key_Space:
            self.paused = not self.paused
            self._update_title()
        elif txt == "f":
            self.following = not self.following
            self._update_title()
            if self.following:
                self._redraw()
        elif txt == "s":
            self.snapshot("microstructure/live/_snap.png")
        elif txt == "c":
            self._center_view()
        elif txt == "t":
            self.clustered = not self.clustered
            if not self.paused:
                self._redraw()
        elif txt and txt.isdigit() and txt != "0":
            idx = int(txt) - 1
            if idx < len(self.engine.symbols):
                self.symbol = self.engine.symbols[idx]
                # auto-frame the new symbol on its own price scale + re-follow
                self._center_view()
        super().keyPressEvent(ev)

    # ---- shutdown --------------------------------------------------------
    async def async_shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        if self._snapshot_path:
            self.snapshot(self._snapshot_path)
        try:
            await self.engine.shutdown()
        except Exception:  # noqa: BLE001
            pass
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, ev) -> None:  # noqa: N802
        # cancel the driver loop; its finally block runs async_shutdown, which
        # is idempotent, so the engine is torn down cleanly and the app quits.
        if self._driver_task is not None and not self._driver_task.done():
            self._driver_task.cancel()
        else:
            asyncio.ensure_future(self.async_shutdown())
        ev.accept()


async def _driver(engine: LiveEngine, win: MicroWindow, duration, snapshot, snapshot_every) -> None:
    await engine.start()
    win._update_title()
    elapsed = 0.0
    last_snap = 0.0
    try:
        while True:
            await asyncio.sleep(REFRESH_S)
            win.refresh()
            elapsed += REFRESH_S
            if snapshot and snapshot_every and elapsed - last_snap >= snapshot_every:
                win.snapshot(snapshot); last_snap = elapsed
            if duration is not None and elapsed >= duration:
                break
    except asyncio.CancelledError:
        pass
    finally:
        await win.async_shutdown()


def run_qt(engine: LiveEngine, symbol: str | None = None, duration: float | None = None,
           snapshot: str | None = None, snapshot_every: float | None = None,
           view_seconds: float = DEFAULT_VIEW_S) -> None:
    import qasync

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    win = MicroWindow(engine, symbol, view_seconds=view_seconds)
    win._snapshot_path = None  # per-tick snapshot off; driver handles periodics
    win.show()
    with loop:
        task = loop.create_task(_driver(engine, win, duration, snapshot, snapshot_every))
        win._driver_task = task
        loop.run_forever()
