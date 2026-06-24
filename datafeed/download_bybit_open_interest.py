"""Download Bybit USDT-perp open-interest history -> monthly parquet.

Bybit v5 returns open interest at fixed intervals such as 1h. The strategy
harness forward-fills this series onto the bar grid, so a 1h OI cadence is
enough for 1h decision strategies.

Usage:
    uv run python -m datafeed.download_bybit_open_interest --symbol BTCUSDT --start 2024-01 --end 2025-12
    uv run python -m datafeed.download_bybit_open_interest --all --start 2024-01 --end 2025-12 --workers 4
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
    _month_bounds,
    list_perp_symbols,
    parse_ym,
)
from datafeed.loader import data_root


DATA_ROOT = data_root() / "bybit" / "perp" / "open_interest"
MAX_LIMIT = 200
INTERVAL = "1h"
EXPECTED_TOLERANCE = 0.8


def _expected_count(year: int, month: int) -> int:
    start_ms, end_ms = _month_bounds(year, month)
    return int((end_ms - start_ms) // (60 * 60 * 1000))


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


def _parse_rows(rows: list[dict]) -> pd.DataFrame:
    out = []
    for row in rows:
        ts = row.get("timestamp")
        oi = row.get("openInterest")
        if ts is None or oi is None:
            continue
        out.append({"timestamp": int(ts), "open_interest": float(oi)})
    if not out:
        return pd.DataFrame(columns=["timestamp", "open_interest"])
    return pd.DataFrame(out)


def fetch_month(ex: ccxt.bybit, symbol: str, year: int, month: int) -> pd.DataFrame:
    start_ms, end_ms = _month_bounds(year, month)
    rows: list[dict] = []
    since = start_ms
    while since < end_ms:
        resp = ex.publicGetV5MarketOpenInterest({
            "category": "linear",
            "symbol": symbol,
            "intervalTime": INTERVAL,
            "startTime": since,
            "endTime": min(end_ms - 1, since + MAX_LIMIT * 60 * 60 * 1000),
            "limit": MAX_LIMIT,
        })
        result = resp.get("result") or {}
        batch = result.get("list") or []
        if not batch:
            break
        rows.extend(batch)
        parsed = _parse_rows(batch)
        if parsed.empty:
            break
        last_ts = int(parsed["timestamp"].max())
        if last_ts <= since:
            break
        since = last_ts + 60 * 60 * 1000
        cursor = result.get("nextPageCursor")
        if cursor:
            # Bybit usually does not need cursoring when start/end are narrow,
            # but handle it inside the same time page when present.
            while cursor:
                resp = ex.publicGetV5MarketOpenInterest({
                    "category": "linear",
                    "symbol": symbol,
                    "intervalTime": INTERVAL,
                    "startTime": since,
                    "endTime": min(end_ms - 1, since + MAX_LIMIT * 60 * 60 * 1000),
                    "limit": MAX_LIMIT,
                    "cursor": cursor,
                })
                result = resp.get("result") or {}
                batch = result.get("list") or []
                if not batch:
                    break
                rows.extend(batch)
                cursor = result.get("nextPageCursor")
        time.sleep(0.02)

    df = _parse_rows(rows)
    if df.empty:
        return df
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
        expected = _expected_count(y, m)
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
            time.sleep(0.05)
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return fetched


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--start", default="2024-01")
    ap.add_argument("--end", default="2025-12")
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
        print(f"Downloading open interest for {len(symbols)} symbols, {args.start} -> {args.end}")
    else:
        ap.error("specify --symbol or --all")

    months_per_sym = (end_ym[0] - start_ym[0]) * 12 + (end_ym[1] - start_ym[1]) + 1
    pbar = tqdm(total=months_per_sym * len(symbols), desc="oi-months", unit="mo")

    if len(symbols) == 1 or args.workers == 1:
        for sym in symbols:
            download_symbol_range(sym, start_ym, end_ym, force=args.force, pbar=pbar)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {
                pool.submit(download_symbol_range, sym, start_ym, end_ym, args.force, pbar): sym
                for sym in symbols
            }
            for fut in as_completed(futs):
                sym = futs[fut]
                try:
                    fut.result()
                except Exception as e:
                    tqdm.write(f"[{sym}] FAILED: {e}")
    pbar.close()


if __name__ == "__main__":
    main()

