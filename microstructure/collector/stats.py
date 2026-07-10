"""Live metrics registry, keyed by (symbol, stream).

The watch loops and the writer update these counters; the UI (or the headless
status printer) reads snapshots. Deliberately lock-free: everything runs in one
asyncio event loop, so plain attribute writes are safe.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class StreamStat:
    symbol: str
    stream: str
    state: str = "starting"        # starting | live | paused | error | stopped
    messages: int = 0              # payloads received
    rows: int = 0                  # rows written to buffer (>= messages for trades)
    rows_flushed: int = 0
    bytes_written: int = 0
    parts: int = 0
    last_event_ms: int | None = None   # ts_exchange of most recent payload
    last_recv_ms: int | None = None    # ts_local of most recent payload
    last_error: str | None = None
    # sliding window of recv timestamps for a msg/s estimate
    _recent: deque = field(default_factory=lambda: deque(maxlen=256))

    def mark(self, n_rows: int, event_ms: int | None) -> None:
        t = now_ms()
        self.messages += 1
        self.rows += n_rows
        self.last_recv_ms = t
        self.last_event_ms = event_ms
        self._recent.append(t)
        if self.state in ("starting", "error"):
            self.state = "live"

    def msgs_per_sec(self) -> float:
        if len(self._recent) < 2:
            return 0.0
        span = (self._recent[-1] - self._recent[0]) / 1000.0
        return (len(self._recent) - 1) / span if span > 0 else 0.0

    def lag_ms(self) -> int | None:
        """Feed lag: local receipt minus exchange event time."""
        if self.last_recv_ms is None or self.last_event_ms is None:
            return None
        return self.last_recv_ms - self.last_event_ms


class StatsRegistry:
    def __init__(self) -> None:
        self._stats: dict[tuple[str, str], StreamStat] = {}
        self.started_ms: int = now_ms()

    def get(self, symbol: str, stream: str) -> StreamStat:
        key = (symbol, stream)
        st = self._stats.get(key)
        if st is None:
            st = StreamStat(symbol=symbol, stream=stream)
            self._stats[key] = st
        return st

    def all(self) -> list[StreamStat]:
        return list(self._stats.values())

    def symbols(self) -> list[str]:
        seen: list[str] = []
        for (sym, _) in self._stats:
            if sym not in seen:
                seen.append(sym)
        return seen

    def uptime_s(self) -> float:
        return (now_ms() - self.started_ms) / 1000.0

    def totals(self) -> dict:
        rows = sum(s.rows for s in self._stats.values())
        msgs = sum(s.messages for s in self._stats.values())
        by = sum(s.bytes_written for s in self._stats.values())
        parts = sum(s.parts for s in self._stats.values())
        return {"rows": rows, "messages": msgs, "bytes": by, "parts": parts}
