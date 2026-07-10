"""Textual dashboard for the realtime polygon.

Layout::

    ┌ totals bar (uptime · window · symbols · msg/s · rec) ──────────┐
    │ DataTable: one row per symbol, one column per registered metric│
    ├───────────────────────────┬────────────────────────────────────┤
    │ detail panel (highlighted) │ trade tape (highlighted symbol)   │
    └ footer: key bindings ─────────────────────────────────────────┘

Keys:  a add symbol · d remove highlighted · q quit

Columns come from ``metrics.REGISTRY`` in registration order, so adding a metric
there adds a column here automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static

from microstructure.live.engine import LiveEngine
from microstructure.live.metrics import REGISTRY
from microstructure.live.state import SymbolState


def _fmt_uptime(s: float) -> str:
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m}m{sec:02d}s"


def _fmt_hms(secs: float) -> str:
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


class AddSymbolScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="add-box"):
            yield Static("Add symbol (e.g. SOLUSDT):", id="add-label")
            yield Input(placeholder="SYMBOLUSDT", id="add-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip().upper() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PolygonApp(App):
    CSS = """
    #totals { height: 1; content-align: left middle; color: $accent; }
    #table { height: 1fr; }
    #bottom { height: 14; }
    #detail { width: 40%; border: round $panel; padding: 0 1; }
    #tape { width: 60%; border: round $panel; padding: 0 1; }
    #add-box { width: 50; height: auto; padding: 1 2; border: thick $accent; background: $surface; }
    """

    BINDINGS = [
        Binding("a", "add_symbol", "add"),
        Binding("d", "remove_symbol", "remove"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, engine: LiveEngine) -> None:
        super().__init__()
        self.engine = engine
        self._row_keys: dict[str, object] = {}
        self._col_keys: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("starting...", id="totals")
        yield DataTable(id="table", zebra_stripes=True, cursor_type="row")
        with Horizontal(id="bottom"):
            yield Static("", id="detail")
            yield Static("", id="tape")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "Bybit microstructure polygon"
        rec = " · REC" if self.engine.record else ""
        self.sub_title = f"window {self.engine.window_s:.0f}s{rec}"
        table = self.query_one("#table", DataTable)
        self._col_keys["symbol"] = table.add_column("symbol", width=11)
        for m in REGISTRY:
            self._col_keys[m.key] = table.add_column(m.label, width=m.width)
        await self.engine.start()
        self.set_interval(0.5, self._refresh)

    def _refresh(self) -> None:
        table = self.query_one("#table", DataTable)
        active = list(self.engine.symbols)

        for sym in list(self._row_keys):
            if sym not in active:
                try:
                    table.remove_row(self._row_keys[sym])
                except Exception:  # noqa: BLE001
                    pass
                del self._row_keys[sym]

        for sym in active:
            state = self.engine.states.get(sym)
            if state is None:
                continue
            values = [sym] + [self._cell(m.fn(state), m.key, state) for m in REGISTRY]
            if sym in self._row_keys:
                rk = self._row_keys[sym]
                for ck, val in zip(self._col_keys.values(), values):
                    table.update_cell(rk, ck, val)
            else:
                self._row_keys[sym] = table.add_row(*values, key=sym)

        self._update_totals()
        self._update_detail_and_tape()

    @staticmethod
    def _cell(text: str, key: str, state: SymbolState) -> Text:
        """Colour a few sign-carrying cells; leave the rest plain."""
        style = ""
        if key in ("cvd_win", "cvd_sess", "imb5", "doi_win", "funding", "premium"):
            if text.startswith("+"):
                style = "green"
            elif text.startswith("-") and text != "-":
                style = "red"
        elif key == "regime":
            style = {"new longs": "bold green", "short cover": "yellow",
                     "new shorts": "bold red", "long exit": "magenta"}.get(text, "")
        elif key == "buy_pct" and text != "-":
            try:
                style = "green" if float(text) >= 50 else "red"
            except ValueError:
                style = ""
        return Text(text, style=style)

    def _update_totals(self) -> None:
        t = self.engine.stats.totals()
        up = _fmt_uptime(self.engine.stats.uptime_s())
        msgs_s = sum(s.msgs_per_sec() for s in self.engine.stats.all())
        rec = f" · REC {t['parts']} parts" if self.engine.record else ""
        self.query_one("#totals", Static).update(
            f"uptime {up}  ·  window {self.engine.window_s:.0f}s  ·  "
            f"symbols {len(self.engine.symbols)}  ·  msg/s {msgs_s:.0f}  ·  "
            f"msgs {t['messages']:,}{rec}"
        )

    def _highlighted_symbol(self) -> str | None:
        table = self.query_one("#table", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:  # noqa: BLE001
            return None

    def _update_detail_and_tape(self) -> None:
        sym = self._highlighted_symbol()
        detail = self.query_one("#detail", Static)
        tape_w = self.query_one("#tape", Static)
        if sym is None or sym not in self.engine.states:
            detail.update("")
            tape_w.update("")
            return
        s = self.engine.states[sym]

        d = Text()
        d.append(f"{sym}\n", style="bold")
        price = s.price()
        d.append(f"  price     {price:,.2f}\n" if price else "  price     -\n")
        d.append(f"  regime    {s.regime()}\n")
        dp, doi = s.dprice_window(), s.doi_window()
        d.append(f"  Δprice {self.engine.window_s:.0f}s {dp:+,.2f}\n" if dp is not None else "  Δprice    -\n")
        d.append(f"  ΔOI {self.engine.window_s:.0f}s    {doi:+,.1f}\n" if doi is not None else "  ΔOI       -\n")
        d.append(f"  CVD win   {s.cvd_window():+,.2f}\n")
        d.append(f"  CVD sess  {s.cvd_session():+,.1f}\n")
        fr = s.funding_rate()
        if fr is not None:
            d.append(f"  funding   {1e4*fr:+.2f} bp  (in {_fmt_hms(s.secs_to_funding())})\n")
        d.append(f"  liq sess  {s.n_liq_session}\n")
        detail.update(d)

        tape = Text()
        tape.append(f"tape · {sym} (last {min(len(s.tape), 14)})\n", style="bold")
        for (ts, side, amt, px) in list(s.tape)[-14:][::-1]:
            hhmmss = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M:%S")
            style = "green" if side == "buy" else "red"
            arrow = "▲" if side == "buy" else "▼"
            tape.append(f"  {hhmmss}  ", style="dim")
            tape.append(f"{arrow} {side:<4} {amt:>10.4f} @ {px:,.2f}\n", style=style)
        tape_w.update(tape)

    async def action_add_symbol(self) -> None:
        symbol = await self.push_screen_wait(AddSymbolScreen())
        if symbol:
            await self.engine.add_symbol(symbol)

    async def action_remove_symbol(self) -> None:
        sym = self._highlighted_symbol()
        if sym:
            await self.engine.remove_symbol(sym)

    async def action_quit(self) -> None:
        self.query_one("#totals", Static).update("shutting down...")
        await self.engine.shutdown()
        self.exit()


def run_ui(engine: LiveEngine) -> None:
    PolygonApp(engine).run()
