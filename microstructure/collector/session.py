"""Capture session orchestrator.

Owns the shared ccxt.pro client, the stats registry, the per-(symbol,stream)
sinks and watch tasks, and the on-disk session directory + manifest. Symbols
can be added, paused/resumed, and removed while the session runs; the UI and the
headless runner both drive the session through this object.

On-disk layout::

    data/bybit/micro/sessions/<session_id>/
        manifest.json
        orderbook/BTCUSDT/part-00001.parquet
        trades/BTCUSDT/part-00001.parquet
        ...
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from datafeed.loader import data_root

from microstructure.collector import schemas
from microstructure.collector.exchange import make_exchange, validate_symbols
from microstructure.collector.stats import StatsRegistry
from microstructure.collector.streams import RUNNERS, StreamControl
from microstructure.collector.writer import Sink


def _default_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def sessions_root() -> Path:
    return data_root() / "bybit" / "micro" / "sessions"


@dataclass
class SessionConfig:
    symbols: list[str]
    streams: list[str]
    depth: int = 25
    sample_interval_ms: int = 250      # order-book snapshot cadence
    flush_interval_s: float = 60.0     # parquet part rotation
    session_id: str = field(default_factory=_default_session_id)


class CaptureSession:
    def __init__(self, config: SessionConfig) -> None:
        self.config = config
        self.dir = sessions_root() / config.session_id
        self.stats = StatsRegistry()
        self.ex = make_exchange()
        self._controls: dict[str, StreamControl] = {}
        self._tasks: dict[str, list[asyncio.Task]] = {}
        self._sinks: dict[tuple[str, str], Sink] = {}
        self._log: list[str] = []
        self._started_iso: str | None = None
        self._lock = asyncio.Lock()

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._started_iso = datetime.now(timezone.utc).isoformat()
        valid, unknown = await validate_symbols(self.ex, self.config.symbols)
        for s in unknown:
            self.log(f"skip unknown symbol: {s}")
        self.config.symbols = []
        for sym in valid:
            await self.add_symbol(sym)
        self._write_manifest()
        if not self.config.symbols:
            self.log("no valid symbols — nothing to capture")

    async def add_symbol(self, symbol: str) -> bool:
        symbol = symbol.strip().upper()
        async with self._lock:
            if symbol in self._controls:
                self.log(f"{symbol} already active")
                return False
            # validate lazily for symbols added at runtime
            if symbol not in [s.upper() for s in self.config.symbols]:
                valid, unknown = await validate_symbols(self.ex, [symbol])
                if not valid:
                    self.log(f"reject unknown symbol: {symbol}")
                    return False
                symbol = valid[0]
            ctl = StreamControl(stop=asyncio.Event())
            self._controls[symbol] = ctl
            tasks: list[asyncio.Task] = []
            for stream in self.config.streams:
                stat = self.stats.get(symbol, stream)
                cols = schemas.columns_for(stream, self.config.depth)
                sink = Sink(self.dir, stream, symbol, cols, stat,
                            flush_interval_s=self.config.flush_interval_s)
                sink.start()
                self._sinks[(symbol, stream)] = sink
                runner = RUNNERS[stream]
                if stream == "orderbook":
                    coro = runner(self.ex, symbol, sink, stat, ctl,
                                  self.config.depth, self.config.sample_interval_ms)
                else:
                    coro = runner(self.ex, symbol, sink, stat, ctl)
                tasks.append(asyncio.create_task(coro, name=f"{stream}:{symbol}"))
            self._tasks[symbol] = tasks
            if symbol not in self.config.symbols:
                self.config.symbols.append(symbol)
            self.log(f"+ {symbol} ({', '.join(self.config.streams)})")
        self._write_manifest()
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
            for stream in self.config.streams:
                sink = self._sinks.pop((symbol, stream), None)
                if sink is not None:
                    await sink.stop()
            self._controls.pop(symbol, None)
            self._tasks.pop(symbol, None)
            if symbol in self.config.symbols:
                self.config.symbols.remove(symbol)
            self.log(f"- {symbol} (stopped + flushed)")
        self._write_manifest()
        return True

    def toggle_pause(self, symbol: str) -> bool | None:
        ctl = self._controls.get(symbol.strip().upper())
        if ctl is None:
            return None
        ctl.paused = not ctl.paused
        self.log(f"{'paused' if ctl.paused else 'resumed'} {symbol}")
        return ctl.paused

    def is_paused(self, symbol: str) -> bool:
        ctl = self._controls.get(symbol.strip().upper())
        return bool(ctl and ctl.paused)

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
        self._write_manifest(final=True)
        self.log("session closed — all buffers flushed")

    # ---- manifest & log --------------------------------------------------
    def _write_manifest(self, final: bool = False) -> None:
        totals = self.stats.totals()
        manifest = {
            "session_id": self.config.session_id,
            "exchange": "bybit",
            "market": "linear-perp",
            "symbols": sorted(self.config.symbols),
            "streams": self.config.streams,
            "depth": self.config.depth,
            "sample_interval_ms": self.config.sample_interval_ms,
            "flush_interval_s": self.config.flush_interval_s,
            "started": self._started_iso,
            "ended": datetime.now(timezone.utc).isoformat() if final else None,
            "totals": totals,
        }
        tmp = self.dir / "manifest.json.tmp"
        tmp.write_text(json.dumps(manifest, indent=2))
        tmp.replace(self.dir / "manifest.json")

    def log(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"{ts}  {msg}"
        self._log.append(line)
        self._log = self._log[-500:]

    def recent_log(self, n: int = 12) -> list[str]:
        return self._log[-n:]
