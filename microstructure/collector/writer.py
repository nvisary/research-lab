"""Buffered rolling parquet writer (Sink).

One Sink per (symbol, stream). Rows are appended to an in-memory buffer by the
watch loop; a background task flushes the buffer to a new zstd parquet *part*
file every `flush_interval_s` seconds (or sooner if `max_buffer_rows` is hit).

Rolling separate part files -- rather than appending row groups to one growing
file -- keeps the format crash-safe: a hard kill loses at most the current
in-memory buffer, and every already-written part is a complete, readable file.
The actual parquet encode runs in a worker thread so it never stalls the event
loop that is servicing the WebSocket feeds.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pandas as pd

from microstructure.collector.stats import StreamStat


class Sink:
    def __init__(
        self,
        session_dir: Path,
        stream: str,
        symbol: str,
        columns: list[str],
        stat: StreamStat,
        flush_interval_s: float = 60.0,
        max_buffer_rows: int = 50_000,
    ) -> None:
        self.dir = session_dir / stream / symbol
        self.stream = stream
        self.symbol = symbol
        self.columns = columns
        self.stat = stat
        self.flush_interval_s = flush_interval_s
        self.max_buffer_rows = max_buffer_rows

        self._buffer: list[dict] = []
        self._part = 0
        self._flush_lock = asyncio.Lock()
        self._flush_now = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False

    def append(self, row: dict) -> None:
        self._buffer.append(row)
        if len(self._buffer) >= self.max_buffer_rows:
            self._flush_now.set()

    def append_many(self, rows: list[dict]) -> None:
        self._buffer.extend(rows)
        if len(self._buffer) >= self.max_buffer_rows:
            self._flush_now.set()

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._run(), name=f"flush:{self.stream}:{self.symbol}")

    async def _run(self) -> None:
        try:
            while not self._stopping:
                try:
                    await asyncio.wait_for(self._flush_now.wait(), timeout=self.flush_interval_s)
                except asyncio.TimeoutError:
                    pass
                self._flush_now.clear()
                await self.flush()
        except asyncio.CancelledError:
            pass
        finally:
            await self.flush()  # drain whatever is left on shutdown

    async def flush(self) -> int:
        async with self._flush_lock:
            if not self._buffer:
                return 0
            batch, self._buffer = self._buffer, []
            self._part += 1
            path = self.dir / f"part-{self._part:05d}.parquet"
            n = await asyncio.to_thread(self._write_parquet, batch, path)
            size = path.stat().st_size if path.exists() else 0
            self.stat.rows_flushed += n
            self.stat.bytes_written += size
            self.stat.parts += 1
            return n

    def _write_parquet(self, batch: list[dict], path: Path) -> int:
        df = pd.DataFrame(batch, columns=self.columns)
        df.to_parquet(path, compression="zstd", index=False)
        return len(df)

    async def stop(self) -> None:
        self._stopping = True
        self._flush_now.set()
        if self._task is not None:
            await self._task
