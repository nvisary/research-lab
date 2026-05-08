"""Download Bybit USDT-perp funding rate history -> monthly parquet.

Bybit pays funding every 8h on linear perps (00:00 / 08:00 / 16:00 UTC).
Idempotent — re-running skips months already complete on disk.

Usage:
    uv run python -m datafeed.download_bybit_funding --symbol BTCUSDT --start 2024-01 --end 2026-04
    uv run python -m datafeed.download_bybit_funding --all --launched-before 2024-01-01 \
        --start 2024-01 --end 2026-04 --workers 6
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd
from tqdm import tqdm

from datafeed.download_bybit import (
    _exchange,
    _to_unified,
    _month_bounds,
    list_perp_symbols,
    parse_ym,
)

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "bybit" / "perp" / "funding"
MAX_LIMIT = 200  # bybit cap for fetch_funding_rate_history

# Expected funding payments per month: ~ 3/day × days_in_month. Bybit can shift
# schedule occasionally; we accept any month with ≥ 80% of expected as "complete".
EXPECTED_TOLERANCE = 0.8


def _expected_funding_count(year: int, month: int) -> int:
    s, e = _month_bounds(year, month)
    days = (e - s) // (24 * 3600 * 1000)
    return int(days * 3)


def _partition_path(symbol: str, year: int, month: int) -> Path:
    return DATA_ROOT / symbol / f"{year:04d}-{month:02d}.parquet"


def _is_complete(path: Path, expected: int) -> bool:
    if not path.exists():
        return False
    try:
        n = len(pd.read_parquet(path, columns=["timestamp"]))
    except Exception:
        return False
    return n >= expected * EXPECTED_TOLERANCE


def fetch_month(ex: ccxt.bybit, symbol: str, year: int, month: int) -> pd.DataFrame:
    unified = _to_unified(symbol)
    start_ms, end_ms = _month_bounds(year, month)
    rows: list[dict] = []
    since = start_ms
    while since < end_ms:
        batch = ex.fetch_funding_rate_history(unified, since=since, limit=MAX_LIMIT)
        if not batch:
            break
        rows.extend(batch)
        last_ts = int(batch[-1]["timestamp"])
        if last_ts <= since:
            break
        since = last_ts + 1
        if last_ts >= end_ms:
            break

    if not rows:
        return pd.DataFrame(columns=["timestamp", "rate"])

    df = pd.DataFrame([
        {"timestamp": int(r["timestamp"]), "rate": float(r["fundingRate"])}
        for r in rows
    ])
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df = df[(df["timestamp"] >= start_ms) & (df["timestamp"] < end_ms)]
    return df


def download_symbol_range(symbol: str, start_ym: tuple[int, int], end_ym: tuple[int, int],
                          force: bool = False, pbar: tqdm | None = None) -> int:
    ex = _exchange()
    fetched = 0
    y, m = start_ym
    ey, em = end_ym
    while (y, m) <= (ey, em):
        path = _partition_path(symbol, y, m)
        expected = _expected_funding_count(y, m)
        if not force and _is_complete(path, expected):
            if pbar is not None:
                pbar.update(1)
        else:
            try:
                df = fetch_month(ex, symbol, y, m)
                path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(path, compression="zstd", index=False)
                fetched += 1
            except Exception as e:
                if pbar is not None:
                    tqdm.write(f"[{symbol} {y:04d}-{m:02d}] FAILED: {e}")
            if pbar is not None:
                pbar.update(1)
            time.sleep(0.05)  # be polite — funding endpoint is shared
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return fetched


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--start", default="2024-01")
    ap.add_argument("--end", default="2026-04")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--launched-before", default=None)
    args = ap.parse_args()

    start_ym = parse_ym(args.start)
    end_ym = parse_ym(args.end)

    if args.symbol:
        symbols = [args.symbol]
    elif args.all:
        all_perps = list_perp_symbols()
        if args.launched_before:
            cutoff_ms = int(datetime.fromisoformat(args.launched_before)
                            .replace(tzinfo=timezone.utc).timestamp() * 1000)
            kept = [s for s in all_perps if 0 < s.get("launchTime", 0) < cutoff_ms]
            print(f"{len(kept)}/{len(all_perps)} perps launched before {args.launched_before}")
            symbols = [s["symbol"] for s in kept]
        else:
            symbols = [s["symbol"] for s in all_perps]
        print(f"Downloading funding for {len(symbols)} symbols, {args.start} -> {args.end}")
    else:
        ap.error("specify --symbol or --all")

    months_per_sym = (end_ym[0] - start_ym[0]) * 12 + (end_ym[1] - start_ym[1]) + 1
    pbar = tqdm(total=months_per_sym * len(symbols), desc="funding-months", unit="mo")

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
