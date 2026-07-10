"""Textual dashboard for the live collector.

Layout::

    ┌ totals bar (uptime · rows · msgs · parts · disk) ──────────────┐
    │ DataTable: one row per symbol, msg/s per stream + lag/rows/disk│
    ├────────────────────────────────────────────────────────────────┤
    │ session log (scrolling)                                        │
    └ footer: key bindings ─────────────────────────────────────────┘

Keys:
    a  add a symbol (prompt)      p  pause/resume highlighted symbol
    d  remove highlighted symbol  q  quit (flushes all buffers)

The Textual app and the ccxt.pro watch tasks share one asyncio event loop, so
the session is started in on_mount and torn down on quit.
"""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Log, Static

from microstructure.collector.session import CaptureSession, SessionConfig


def _fmt_bytes(n: float) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:.0f}{unit}" if unit == "B" else f"{x:.1f}{unit}"
        x /= 1024
    return f"{x:.1f}GB"


def _fmt_uptime(s: float) -> str:
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{sec:02d}s"
    return f"{m}m{sec:02d}s"


class AddSymbolScreen(ModalScreen[str | None]):
    """Small modal prompting for a symbol to add."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="add-box"):
            yield Static("Add symbol (e.g. SOLUSDT):", id="add-label")
            yield Input(placeholder="SYMBOLUSDT", id="add-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip().upper() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CollectorApp(App):
    CSS = """
    #totals { height: 1; content-align: left middle; color: $accent; }
    DataTable { height: 1fr; }
    #log { height: 10; border: round $panel; }
    #add-box { width: 50; height: auto; padding: 1 2; border: thick $accent; background: $surface; }
    """

    BINDINGS = [
        Binding("a", "add_symbol", "add"),
        Binding("p", "toggle_pause", "pause/resume"),
        Binding("d", "remove_symbol", "remove"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, config: SessionConfig) -> None:
        super().__init__()
        self.session = CaptureSession(config)
        self._row_keys: dict[str, object] = {}
        self._col_keys: dict[str, object] = {}
        self._log_len = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("starting...", id="totals")
        yield DataTable(id="table", zebra_stripes=True, cursor_type="row")
        yield Log(id="log", highlight=False)
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "Bybit microstructure collector"
        table = self.query_one("#table", DataTable)
        self._col_keys["symbol"] = table.add_column("symbol", width=12)
        for stream in self.session.config.streams:
            label = {"orderbook": "book/s", "trades": "trades/s",
                     "ticker": "ticker/s", "liquidations": "liq/s"}[stream]
            self._col_keys[stream] = table.add_column(label, width=9)
        self._col_keys["lag"] = table.add_column("lag ms", width=8)
        self._col_keys["rows"] = table.add_column("rows", width=10)
        self._col_keys["disk"] = table.add_column("disk", width=8)
        self._col_keys["state"] = table.add_column("state", width=9)

        self.sub_title = f"session {self.session.config.session_id}"
        await self.session.start()
        self.set_interval(0.5, self._refresh)

    def _refresh(self) -> None:
        table = self.query_one("#table", DataTable)
        active = list(self.session.config.symbols)

        # drop rows for removed symbols
        for sym in list(self._row_keys):
            if sym not in active:
                try:
                    table.remove_row(self._row_keys[sym])
                except Exception:  # noqa: BLE001
                    pass
                del self._row_keys[sym]

        for sym in active:
            paused = self.session.is_paused(sym)
            per_stream: dict[str, str] = {}
            lag_vals, rows_total, bytes_total, states = [], 0, 0, []
            for stream in self.session.config.streams:
                st = self.session.stats.get(sym, stream)
                per_stream[stream] = f"{st.msgs_per_sec():.1f}"
                lag = st.lag_ms()
                if lag is not None:
                    lag_vals.append(lag)
                rows_total += st.rows
                bytes_total += st.bytes_written
                states.append(st.state)
            lag_disp = "-" if not lag_vals else str(max(lag_vals))
            state_disp = "paused" if paused else ("error" if "error" in states else "live")

            values = [sym]
            for stream in self.session.config.streams:
                values.append(per_stream[stream])
            values += [lag_disp, f"{rows_total:,}", _fmt_bytes(bytes_total), state_disp]

            if sym in self._row_keys:
                rk = self._row_keys[sym]
                for ck, val in zip(self._col_keys.values(), values):
                    table.update_cell(rk, ck, val)
            else:
                self._row_keys[sym] = table.add_row(*values, key=sym)

        t = self.session.stats.totals()
        up = _fmt_uptime(self.session.stats.uptime_s())
        self.query_one("#totals", Static).update(
            f"uptime {up}  ·  rows {t['rows']:,}  ·  msgs {t['messages']:,}  ·  "
            f"parts {t['parts']}  ·  disk {_fmt_bytes(t['bytes'])}"
        )

        log = self.query_one("#log", Log)
        lines = self.session._log
        if len(lines) > self._log_len:
            for line in lines[self._log_len:]:
                log.write_line(line)
            self._log_len = len(lines)

    def _highlighted_symbol(self) -> str | None:
        table = self.query_one("#table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            return row_key.value
        except Exception:  # noqa: BLE001
            return None

    async def action_add_symbol(self) -> None:
        symbol = await self.push_screen_wait(AddSymbolScreen())
        if symbol:
            await self.session.add_symbol(symbol)

    def action_toggle_pause(self) -> None:
        sym = self._highlighted_symbol()
        if sym:
            self.session.toggle_pause(sym)

    async def action_remove_symbol(self) -> None:
        sym = self._highlighted_symbol()
        if sym:
            await self.session.remove_symbol(sym)

    async def action_quit(self) -> None:
        self.query_one("#totals", Static).update("shutting down — flushing buffers...")
        await self.session.shutdown()
        self.exit()


def run_ui(config: SessionConfig) -> None:
    CollectorApp(config).run()
