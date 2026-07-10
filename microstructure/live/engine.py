"""Live polygon engine.

Reuses the collector's resilient ccxt.pro watch loops (``collector.streams``)
verbatim: those loops push rows into anything exposing ``append``/``append_many``.
We pass a :class:`Tap` instead of a parquet ``Sink`` — the tap forwards each row
into the per-symbol :class:`SymbolState` (live metrics) and, when ``record`` is
on, *also* into a real ``Sink`` so the session lands on disk exactly like a
collector capture.

Symbols can be added/removed while running, mirroring ``collector.session``.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from datafeed.loader import data_root

from microstructure.collector import schemas
from microstructure.collector.exchange import make_exchange, validate_symbols
from microstructure.collector.stats import StatsRegistry
from microstructure.collector.streams import RUNNERS, StreamControl
from microstructure.collector.writer import Sink
from microstructure.live.state import SymbolState


def sessions_root() -> Path:
    return data_root() / "bybit" / "micro" / "sessions"


class Tap:
    """A sink-shaped shim: routes collector rows to live state (+ optional disk)."""

    def __init__(self, symbol: str, stream: str, state: SymbolState, rec: Sink | None = None) -> None:
        self.symbol = symbol
        self.stream = stream
        self._handler = getattr(state, f"on_{stream}")
        self._rec = rec

    def append(self, row: dict) -> None:
        self._handler(row)
        if self._rec is not None:
            self._rec.append(row)

    def append_many(self, rows: list[dict]) -> None:
        for r in rows:
            self._handler(r)
        if self._rec is not None:
            self._rec.append_many(rows)


class LiveEngine:
    def __init__(self, symbols: list[str], streams: list[str], window_s: float = 60.0,
                 depth: int = 25, sample_interval_ms: int = 250,
                 record: bool = False, flush_interval_s: float = 60.0,
                 session_id: str | None = None) -> None:
        self.requested = [s.strip().upper() for s in symbols]
        self.streams = streams
        self.window_s = window_s
        self.depth = depth
        self.sample_interval_ms = sample_interval_ms
        self.record = record
        self.flush_interval_s = flush_interval_s
        self.session_id = session_id or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

        self.ex = make_exchange()
        self.stats = StatsRegistry()
        self.symbols: list[str] = []
        self.states: dict[str, SymbolState] = {}
        self._controls: dict[str, StreamControl] = {}
        self._tasks: dict[str, list[asyncio.Task]] = {}
        self._sinks: dict[tuple[str, str], Sink] = {}
        self._log: list[str] = []
        self._lock = asyncio.Lock()
        self.rec_dir = sessions_root() / self.session_id if record else None
        self._started_iso: str | None = None

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self._started_iso = datetime.now(timezone.utc).isoformat()
        if self.rec_dir is not None:
            self.rec_dir.mkdir(parents=True, exist_ok=True)
        valid, unknown = await validate_symbols(self.ex, self.requested)
        for s in unknown:
            self.log(f"skip unknown symbol: {s}")
        for sym in valid:
            await self.add_symbol(sym)
        if self.record:
            self._write_manifest()
        if not self.symbols:
            self.log("no valid symbols")

    async def add_symbol(self, symbol: str) -> bool:
        symbol = symbol.strip().upper()
        async with self._lock:
            if symbol in self._controls:
                self.log(f"{symbol} already active")
                return False
            if symbol not in self.symbols:
                valid, _ = await validate_symbols(self.ex, [symbol])
                if not valid:
                    self.log(f"reject unknown symbol: {symbol}")
                    return False
                symbol = valid[0]
            state = SymbolState(symbol, self.window_s)
            self.states[symbol] = state
            ctl = StreamControl(stop=asyncio.Event())
            self._controls[symbol] = ctl
            tasks: list[asyncio.Task] = []
            for stream in self.streams:
                stat = self.stats.get(symbol, stream)
                rec_sink = None
                if self.record and self.rec_dir is not None:
                    cols = schemas.columns_for(stream, self.depth)
                    rec_sink = Sink(self.rec_dir, stream, symbol, cols, stat,
                                    flush_interval_s=self.flush_interval_s)
                    rec_sink.start()
                    self._sinks[(symbol, stream)] = rec_sink
                tap = Tap(symbol, stream, state, rec_sink)
                runner = RUNNERS[stream]
                if stream == "orderbook":
                    coro = runner(self.ex, symbol, tap, stat, ctl,
                                  self.depth, self.sample_interval_ms)
                else:
                    coro = runner(self.ex, symbol, tap, stat, ctl)
                tasks.append(asyncio.create_task(coro, name=f"{stream}:{symbol}"))
            self._tasks[symbol] = tasks
            self.symbols.append(symbol)
            self.log(f"+ {symbol} ({', '.join(self.streams)})")
        return True

    async def remove_symbol(self, symbol: str) -> bool:
        symbol = symbol.strip().upper()
        async with self._lock:
            ctl = self._controls.get(symbol)
            if ctl is None:
                return False
            ctl.stop.set()
            for task in self._tasks.get(symbol, []):
                task.cancel()
            await asyncio.gather(*self._tasks.get(symbol, []), return_exceptions=True)
            for stream in self.streams:
                sink = self._sinks.pop((symbol, stream), None)
                if sink is not None:
                    await sink.stop()
            self._controls.pop(symbol, None)
            self._tasks.pop(symbol, None)
            self.states.pop(symbol, None)
            if symbol in self.symbols:
                self.symbols.remove(symbol)
            self.log(f"- {symbol}")
        return True

    async def shutdown(self) -> None:
        for ctl in self._controls.values():
            ctl.stop.set()
        all_tasks = [t for ts in self._tasks.values() for t in ts]
        for t in all_tasks:
            t.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)
        for sink in self._sinks.values():
            await sink.stop()
        try:
            await self.ex.close()
        except Exception:  # noqa: BLE001
            pass
        if self.record:
            self._write_manifest(final=True)
        self.log("shut down")

    # ---- manifest & log --------------------------------------------------
    def _write_manifest(self, final: bool = False) -> None:
        if self.rec_dir is None:
            return
        manifest = {
            "session_id": self.session_id,
            "exchange": "bybit",
            "market": "linear-perp",
            "source": "live-polygon",
            "symbols": sorted(self.symbols),
            "streams": self.streams,
            "depth": self.depth,
            "sample_interval_ms": self.sample_interval_ms,
            "flush_interval_s": self.flush_interval_s,
            "started": self._started_iso,
            "ended": datetime.now(timezone.utc).isoformat() if final else None,
            "totals": self.stats.totals(),
        }
        tmp = self.rec_dir / "manifest.json.tmp"
        tmp.write_text(json.dumps(manifest, indent=2))
        tmp.replace(self.rec_dir / "manifest.json")

    def log(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._log.append(f"{ts}  {msg}")
        self._log = self._log[-500:]
