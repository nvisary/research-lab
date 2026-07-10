"""Per-stream ccxt.pro watch loops.

Each coroutine subscribes to one stream for one symbol, updates its StreamStat,
and pushes rows into its Sink. Loops are resilient: a transient WebSocket error
is logged into the stat and retried with a short backoff rather than killing the
capture. A loop exits only when its control's stop event is set.

Pausing (ctl.paused) keeps the subscription warm -- so msg/s and lag stay live
and resume is instant -- but drops rows instead of writing them to disk.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from microstructure.collector import schemas
from microstructure.collector.exchange import to_unified
from microstructure.collector.stats import StreamStat, now_ms
from microstructure.collector.writer import Sink


@dataclass
class StreamControl:
    stop: asyncio.Event
    paused: bool = False


_BACKOFF_S = 2.0


async def _loop(name: str, stat: StreamStat, ctl: StreamControl, step) -> None:
    """Shared retry/backoff wrapper around a single-step watch coroutine."""
    while not ctl.stop.is_set():
        try:
            await step()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 -- resilience: log & retry
            stat.state = "error"
            stat.last_error = f"{type(e).__name__}: {e}"
            try:
                await asyncio.wait_for(ctl.stop.wait(), timeout=_BACKOFF_S)
            except asyncio.TimeoutError:
                pass
    stat.state = "stopped"


async def run_orderbook(ex, symbol, sink: Sink, stat: StreamStat, ctl: StreamControl,
                        depth: int, sample_interval_ms: int) -> None:
    unified = to_unified(symbol)
    sub_limit = schemas.pick_sub_limit(depth)
    last_sample = {"ms": 0}

    async def step():
        ob = await ex.watch_order_book(unified, limit=sub_limit)
        t = now_ms()
        if ctl.paused:
            stat.state = "paused"
            return
        if t - last_sample["ms"] >= sample_interval_ms:
            last_sample["ms"] = t
            sink.append(schemas.orderbook_row(ob, symbol, depth, t))
            stat.mark(1, ob.get("timestamp"))

    await _loop("orderbook", stat, ctl, step)


async def run_trades(ex, symbol, sink: Sink, stat: StreamStat, ctl: StreamControl) -> None:
    unified = to_unified(symbol)

    async def step():
        trades = await ex.watch_trades(unified)
        t = now_ms()
        if ctl.paused:
            stat.state = "paused"
            return
        if not trades:
            return
        rows = [schemas.trade_row(tr, symbol, t) for tr in trades]
        sink.append_many(rows)
        stat.mark(len(rows), trades[-1].get("timestamp"))

    await _loop("trades", stat, ctl, step)


async def run_ticker(ex, symbol, sink: Sink, stat: StreamStat, ctl: StreamControl) -> None:
    unified = to_unified(symbol)

    async def step():
        tk = await ex.watch_ticker(unified)
        t = now_ms()
        if ctl.paused:
            stat.state = "paused"
            return
        sink.append(schemas.ticker_row(tk, symbol, t))
        stat.mark(1, tk.get("timestamp"))

    await _loop("ticker", stat, ctl, step)


async def run_liquidations(ex, symbol, sink: Sink, stat: StreamStat, ctl: StreamControl) -> None:
    unified = to_unified(symbol)

    async def step():
        liqs = await ex.watch_liquidations(unified)
        t = now_ms()
        if ctl.paused:
            stat.state = "paused"
            return
        if not liqs:
            return
        rows = [schemas.liquidation_row(lq, symbol, t) for lq in liqs]
        sink.append_many(rows)
        stat.mark(len(rows), liqs[-1].get("timestamp"))

    await _loop("liquidations", stat, ctl, step)


RUNNERS = {
    "orderbook": run_orderbook,
    "trades": run_trades,
    "ticker": run_ticker,
    "liquidations": run_liquidations,
}
