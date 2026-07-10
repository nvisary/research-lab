"""CLI entry point for the live Bybit microstructure collector.

Examples
--------
Headless capture of two symbols for 10 minutes::

    uv run python -m microstructure.collector \
        --symbols BTCUSDT,ETHUSDT --streams book,trades,ticker,liq \
        --no-ui --duration 600

Interactive Textual dashboard (runs until you quit with 'q')::

    uv run python -m microstructure.collector --symbols BTCUSDT,ETHUSDT
"""
from __future__ import annotations

import argparse
import asyncio
import signal

from microstructure.collector import schemas
from microstructure.collector.session import CaptureSession, SessionConfig


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="microstructure.collector")
    ap.add_argument("--symbols", required=True,
                    help="comma-separated, exchange-native, e.g. BTCUSDT,ETHUSDT")
    ap.add_argument("--streams", default="book,trades,ticker,liq",
                    help="comma-separated: book,trades,ticker,liq (aliases ok)")
    ap.add_argument("--depth", type=int, default=25, help="order-book levels per side")
    ap.add_argument("--sample-ms", type=int, default=250,
                    help="order-book snapshot cadence in milliseconds")
    ap.add_argument("--flush-s", type=float, default=60.0,
                    help="parquet part rotation interval in seconds")
    ap.add_argument("--session-id", default=None, help="override session dir name")
    ap.add_argument("--no-ui", action="store_true", help="headless (no Textual dashboard)")
    ap.add_argument("--duration", type=float, default=None,
                    help="headless only: stop after N seconds (default: run until Ctrl-C)")
    ap.add_argument("--status-every", type=float, default=5.0,
                    help="headless only: status print interval in seconds")
    return ap.parse_args(argv)


def build_config(args: argparse.Namespace) -> SessionConfig:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    streams = schemas.resolve_streams([s for s in args.streams.split(",") if s.strip()])
    kwargs = dict(symbols=symbols, streams=streams, depth=args.depth,
                  sample_interval_ms=args.sample_ms, flush_interval_s=args.flush_s)
    if args.session_id:
        kwargs["session_id"] = args.session_id
    return SessionConfig(**kwargs)


def _fmt_bytes(n: float) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:.0f}{unit}" if unit == "B" else f"{x:.1f}{unit}"
        x /= 1024
    return f"{x:.1f}GB"


def _print_status(session: CaptureSession) -> None:
    t = session.stats.totals()
    up = session.stats.uptime_s()
    print(f"\n[{up:6.0f}s] rows={t['rows']:,} msgs={t['messages']:,} "
          f"parts={t['parts']} disk={_fmt_bytes(t['bytes'])}")
    header = f"  {'symbol':<12}{'stream':<14}{'state':<9}{'msg/s':>8}{'rows':>10}{'lag_ms':>9}"
    print(header)
    for st in sorted(session.stats.all(), key=lambda s: (s.symbol, s.stream)):
        lag = st.lag_ms()
        lag_s = "-" if lag is None else str(lag)
        print(f"  {st.symbol:<12}{st.stream:<14}{st.state:<9}"
              f"{st.msgs_per_sec():>8.1f}{st.rows:>10,}{lag_s:>9}")


async def run_headless(config: SessionConfig, duration: float | None, status_every: float) -> None:
    session = CaptureSession(config)
    stop = asyncio.Event()

    def _request_stop(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, ValueError):
            pass  # Windows: SIGTERM/add_signal_handler unsupported -> KeyboardInterrupt path

    await session.start()
    print(f"session {config.session_id} -> {session.dir}")
    elapsed = 0.0
    try:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=status_every)
            except asyncio.TimeoutError:
                pass
            _print_status(session)
            elapsed += status_every
            if duration is not None and elapsed >= duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        print("\nshutting down — flushing buffers...")
        await session.shutdown()
        _print_status(session)
        print(f"done. session at {session.dir}")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = build_config(args)
    if args.no_ui:
        asyncio.run(run_headless(config, args.duration, args.status_every))
    else:
        from microstructure.collector.ui import run_ui
        run_ui(config)


if __name__ == "__main__":
    main()
