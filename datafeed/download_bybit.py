"""Download Bybit USDT-perp 1m OHLCV → monthly parquet partitions, via ccxt.

Idempotent: re-running skips months already complete on disk.

Usage:
    uv run python -m datafeed.download_bybit --symbol BTCUSDT --start 2025-01 --end 2025-12
    uv run python -m datafeed.download_bybit --all --start 2025-01 --end 2025-12 --workers 8
    uv run python -m datafeed.download_bybit --list-symbols
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd
from tqdm import tqdm

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "bybit" / "perp" / "1m"
META_ROOT = Path(__file__).resolve().parents[1] / "data" / "meta"

MAX_LIMIT = 1000  # bybit cap per fetch_ohlcv
TIMEFRAME = "1m"


def _exchange() -> ccxt.bybit:
    ex = ccxt.bybit({
        "enableRateLimit": True,
        "options": {"defaultType": "swap", "defaultSubType": "linear"},
    })
    return ex


def _to_unified(symbol: str) -> str:
    """BTCUSDT -> BTC/USDT:USDT (ccxt unified linear-swap notation)."""
    if "/" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT:USDT"
    raise ValueError(f"unsupported symbol format: {symbol}")


def list_perp_symbols() -> list[dict]:
    """All linear USDT perpetuals. Cached in data/meta/symbols.json."""
    META_ROOT.mkdir(parents=True, exist_ok=True)
    cache = META_ROOT / "symbols.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 24 * 3600:
        return json.loads(cache.read_text())

    ex = _exchange()
    markets = ex.load_markets()
    out = []
    for m in markets.values():
        if (m.get("swap") and m.get("linear") and m.get("quote") == "USDT"
                and m.get("active") and m.get("settle") == "USDT"):
            out.append({
                "symbol": m["id"],            # exchange-native, e.g. BTCUSDT
                "unified": m["symbol"],       # e.g. BTC/USDT:USDT
                "launchTime": int(m.get("info", {}).get("launchTime", 0) or 0),
            })
    out.sort(key=lambda x: x["symbol"])
    cache.write_text(json.dumps(out, indent=2))
    return out


def _month_bounds(year: int, month: int) -> tuple[int, int]:
    s = datetime(year, month, 1, tzinfo=timezone.utc)
    e = datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=timezone.utc)
    return int(s.timestamp() * 1000), int(e.timestamp() * 1000)


def _expected_rows(year: int, month: int) -> int:
    s, e = _month_bounds(year, month)
    return (e - s) // 60_000


def _partition_path(symbol: str, year: int, month: int) -> Path:
    return DATA_ROOT / symbol / f"{year:04d}-{month:02d}.parquet"


def _is_complete(path: Path, expected: int, tolerance: float = 0.005) -> bool:
    if not path.exists():
        return False
    try:
        n = len(pd.read_parquet(path, columns=["timestamp"]))
    except Exception:
        return False
    return n >= expected * (1 - tolerance)


def fetch_month(ex: ccxt.bybit, symbol: str, year: int, month: int) -> pd.DataFrame:
    """One calendar month of 1m OHLCV via paginated ccxt.fetch_ohlcv."""
    unified = _to_unified(symbol)
    start_ms, end_ms = _month_bounds(year, month)
    rows: list[list] = []
    since = start_ms
    while since < end_ms:
        batch = ex.fetch_ohlcv(unified, timeframe=TIMEFRAME, since=since, limit=MAX_LIMIT)
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= since:
            break
        since = last_ts + 60_000  # next minute

    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.astype({"timestamp": "int64"})
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df = df[(df["timestamp"] >= start_ms) & (df["timestamp"] < end_ms)]
    return df


def download_symbol_range(symbol: str, start_ym: tuple[int, int], end_ym: tuple[int, int],
                          force: bool = False, pbar: tqdm | None = None) -> int:
    ex = _exchange()  # one client per worker — ccxt clients aren't fully thread-safe
    fetched = 0
    y, m = start_ym
    ey, em = end_ym
    while (y, m) <= (ey, em):
        path = _partition_path(symbol, y, m)
        expected = _expected_rows(y, m)
        if not force and _is_complete(path, expected):
            if pbar is not None:
                pbar.update(1)
        else:
            df = fetch_month(ex, symbol, y, m)
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path, compression="zstd", index=False)
            fetched += 1
            if pbar is not None:
                pbar.update(1)
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return fetched


def parse_ym(s: str) -> tuple[int, int]:
    parts = s.split("-")
    return int(parts[0]), int(parts[1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", help="One symbol, e.g. BTCUSDT")
    ap.add_argument("--all", action="store_true", help="All linear USDT perps")
    ap.add_argument("--start", default="2025-01")
    ap.add_argument("--end", default="2025-12")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list-symbols", action="store_true")
    args = ap.parse_args()

    if args.list_symbols:
        syms = list_perp_symbols()
        print(f"{len(syms)} linear USDT perps")
        for s in syms[:20]:
            print(" ", s["symbol"])
        return

    start_ym = parse_ym(args.start)
    end_ym = parse_ym(args.end)

    if args.symbol:
        symbols = [args.symbol]
    elif args.all:
        symbols = [s["symbol"] for s in list_perp_symbols()]
        print(f"Downloading {len(symbols)} symbols, {args.start} → {args.end}")
    else:
        ap.error("specify --symbol or --all")

    months_per_sym = (end_ym[0] - start_ym[0]) * 12 + (end_ym[1] - start_ym[1]) + 1
    pbar = tqdm(total=months_per_sym * len(symbols), desc="months", unit="mo")

    if len(symbols) == 1 or args.workers == 1:
        for sym in symbols:
            download_symbol_range(sym, start_ym, end_ym, force=args.force, pbar=pbar)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(download_symbol_range, sym, start_ym, end_ym, args.force, pbar): sym
                    for sym in symbols}
            for f in as_completed(futs):
                sym = futs[f]
                try:
                    f.result()
                except Exception as e:
                    tqdm.write(f"[{sym}] FAILED: {e}")
    pbar.close()


if __name__ == "__main__":
    main()
