"""CLI entry point for the realtime microstructure polygon.

Examples
--------
Live dashboard for two symbols (60s rolling window)::

    uv run python -m microstructure.live --symbols BTCUSDT,ETHUSDT

Faster window, and record the session to parquet while watching::

    uv run python -m microstructure.live --symbols BTCUSDT --window 30 --record

Headless (no UI) — print a metrics snapshot every few seconds, for a fixed run::

    uv run python -m microstructure.live --symbols BTCUSDT --no-ui --duration 30
"""
from __future__ import annotations

import argparse
import asyncio

from microstructure.collector import schemas
from microstructure.live.engine import LiveEngine
from microstructure.live.metrics import REGISTRY


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="microstructure.live")
    ap.add_argument("--symbols", required=True,
                    help="comma-separated, exchange-native, e.g. BTCUSDT,ETHUSDT")
    ap.add_argument("--streams", default="trades,ticker,book,liq",
                    help="comma-separated: book,trades,ticker,liq (aliases ok)")
    ap.add_argument("--window", type=float, default=60.0,
                    help="rolling metric window in seconds (default 60)")
    ap.add_argument("--depth", type=int, default=25, help="order-book levels per side")
    ap.add_argument("--sample-ms", type=int, default=250,
                    help="order-book snapshot cadence in milliseconds")
    ap.add_argument("--record", action="store_true",
                    help="also persist the session to parquet (like the collector)")
    ap.add_argument("--flush-s", type=float, default=60.0,
                    help="record only: parquet part rotation interval in seconds")
    ap.add_argument("--session-id", default=None, help="record only: override session dir name")
    ap.add_argument("--no-ui", action="store_true", help="headless: print snapshots instead of TUI")
    ap.add_argument("--qt", action="store_true",
                    help="PyQtGraph visual view (heatmap + CVD/force/OI) instead of the Textual table")
    ap.add_argument("--focus", default=None, help="qt: symbol to show (default first)")
    ap.add_argument("--snapshot", default=None,
                    help="qt: also write the current view to this PNG each --snapshot-every")
    ap.add_argument("--snapshot-every", type=float, default=None, help="qt: snapshot interval (s)")
    ap.add_argument("--view-seconds", type=float, default=180.0,
                    help="qt: visible time window in seconds (default 180)")
    ap.add_argument("--duration", type=float, default=None,
                    help="headless/qt: stop after N seconds")
    ap.add_argument("--status-every", type=float, default=3.0,
                    help="headless only: snapshot print interval in seconds")
    return ap.parse_args(argv)


def build_engine(args: argparse.Namespace) -> LiveEngine:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    streams = schemas.resolve_streams([s for s in args.streams.split(",") if s.strip()])
    return LiveEngine(
        symbols=symbols, streams=streams, window_s=args.window,
        depth=args.depth, sample_interval_ms=args.sample_ms,
        record=args.record, flush_interval_s=args.flush_s, session_id=args.session_id,
    )


def _print_snapshot(engine: LiveEngine) -> None:
    up = engine.stats.uptime_s()
    hdr = f"{'symbol':<11}" + "".join(f"{m.label:>11}" for m in REGISTRY)
    print(f"\n[{up:6.0f}s]  window {engine.window_s:.0f}s")
    print(hdr)
    for sym in engine.symbols:
        s = engine.states.get(sym)
        if s is None:
            continue
        print(f"{sym:<11}" + "".join(f"{m.fn(s):>11}" for m in REGISTRY))


async def run_headless(engine: LiveEngine, duration: float | None, status_every: float) -> None:
    await engine.start()
    print(f"live polygon started: {', '.join(engine.symbols)}"
          + (f"  (recording -> {engine.rec_dir})" if engine.record else ""))
    elapsed = 0.0
    try:
        while True:
            await asyncio.sleep(status_every)
            _print_snapshot(engine)
            elapsed += status_every
            if duration is not None and elapsed >= duration:
                break
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("\nshutting down...")
        await engine.shutdown()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.qt and args.depth == 25:
        args.depth = 200   # the heatmap wants a full-depth book
    engine = build_engine(args)
    if args.qt:
        from microstructure.live.qt_ui import run_qt
        run_qt(engine, symbol=args.focus, duration=args.duration,
               snapshot=args.snapshot, snapshot_every=args.snapshot_every,
               view_seconds=args.view_seconds)
    elif args.no_ui:
        asyncio.run(run_headless(engine, args.duration, args.status_every))
    else:
        from microstructure.live.ui import run_ui
        run_ui(engine)


if __name__ == "__main__":
    main()
