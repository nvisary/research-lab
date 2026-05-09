"""Compute Roll-estimator bid-ask spreads for downloaded 1m parquets.

Idempotent: re-running skips months whose spread parquet already exists.

Usage:
    uv run python -m datafeed.estimate_spreads --symbol BTCUSDT --start 2024-01 --end 2026-04
    uv run python -m datafeed.estimate_spreads --all --start 2024-01 --end 2026-04 --workers 8
    uv run python -m datafeed.estimate_spreads --symbol BTCUSDT --report   # print per-month summary

Output layout:
    data/meta/spreads/<SYMBOL>/<YYYY-MM>.parquet
        columns: bucket_start, spread_bps, n_bars, fallback_used
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from datafeed.loader import data_root, available_symbols
from datafeed.spreads import estimate_spread_series, summarize

DATA_ROOT_1M = data_root() / "bybit" / "perp" / "1m"
SPREAD_ROOT = data_root() / "meta" / "spreads"


def _spread_path(symbol: str, year: int, month: int) -> Path:
    return SPREAD_ROOT / symbol / f"{year:04d}-{month:02d}.parquet"


def _ohlcv_path(symbol: str, year: int, month: int) -> Path:
    return DATA_ROOT_1M / symbol / f"{year:04d}-{month:02d}.parquet"


def _months_between(start_ym: tuple[int, int], end_ym: tuple[int, int]) -> list[tuple[int, int]]:
    out = []
    y, m = start_ym
    ey, em = end_ym
    while (y, m) <= (ey, em):
        out.append((y, m))
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def estimate_one_month(symbol: str, year: int, month: int,
                       bucket: str = "1h", force: bool = False) -> dict:
    """Estimate spreads for one symbol-month. Returns a status dict."""
    out_path = _spread_path(symbol, year, month)
    if out_path.exists() and not force:
        return {"symbol": symbol, "month": f"{year:04d}-{month:02d}", "status": "skipped"}

    ohlcv = _ohlcv_path(symbol, year, month)
    if not ohlcv.exists():
        return {"symbol": symbol, "month": f"{year:04d}-{month:02d}", "status": "no_ohlcv"}

    try:
        df = pd.read_parquet(ohlcv)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp").sort_index()
        spread_df = estimate_spread_series(df, bucket=bucket)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        spread_df.to_parquet(out_path, compression="zstd", index=False)
        s = summarize(spread_df)
        return {"symbol": symbol, "month": f"{year:04d}-{month:02d}", "status": "ok",
                **s}
    except Exception as e:
        return {"symbol": symbol, "month": f"{year:04d}-{month:02d}",
                "status": "error", "error": f"{type(e).__name__}: {e}"}


def estimate_symbol_range(symbol: str, start_ym: tuple[int, int],
                          end_ym: tuple[int, int], bucket: str = "1h",
                          force: bool = False) -> list[dict]:
    """Walk months for one symbol. Returns list of per-month status dicts."""
    out = []
    for y, m in _months_between(start_ym, end_ym):
        out.append(estimate_one_month(symbol, y, m, bucket=bucket, force=force))
    return out


def parse_ym(s: str) -> tuple[int, int]:
    parts = s.split("-")
    return int(parts[0]), int(parts[1])


def cmd_report(symbol: str, start_ym: tuple[int, int], end_ym: tuple[int, int]) -> None:
    """Print per-month summary for an already-estimated symbol."""
    print(f"\n=== {symbol} spread report ({start_ym[0]:04d}-{start_ym[1]:02d} "
          f"-> {end_ym[0]:04d}-{end_ym[1]:02d}) ===")
    print(f"{'month':<10} {'mean':>7} {'p50':>7} {'p95':>7} {'fb%':>6} {'n_buck':>7}")
    print("-" * 50)
    for y, m in _months_between(start_ym, end_ym):
        p = _spread_path(symbol, y, m)
        if not p.exists():
            print(f"{y:04d}-{m:02d}    (no data)")
            continue
        df = pd.read_parquet(p)
        s = summarize(df)
        print(f"{y:04d}-{m:02d}   {s['mean_bps']:>6.2f}  {s['median_bps']:>6.2f}  "
              f"{s['p95_bps']:>6.2f}  {s['fallback_pct']:>5.1f}  {s['n_buckets']:>6d}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", help="One symbol, e.g. BTCUSDT")
    ap.add_argument("--all", action="store_true", help="All downloaded symbols")
    ap.add_argument("--start", default="2024-01")
    ap.add_argument("--end", default="2026-04")
    ap.add_argument("--bucket", default="1h",
                    help="Bucket size for intraday spread profile (e.g. '1h', '4h', '1D')")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing spread parquets")
    ap.add_argument("--report", action="store_true",
                    help="Print per-month summary instead of estimating")
    args = ap.parse_args()

    start_ym = parse_ym(args.start)
    end_ym = parse_ym(args.end)

    if args.symbol:
        symbols = [args.symbol]
    elif args.all:
        symbols = available_symbols()
        if not symbols:
            print("No symbols downloaded under data/bybit/perp/1m/", file=sys.stderr)
            sys.exit(1)
    else:
        ap.error("specify --symbol or --all")

    if args.report:
        for sym in symbols:
            cmd_report(sym, start_ym, end_ym)
        return

    months = _months_between(start_ym, end_ym)
    total = len(symbols) * len(months)
    print(f"Estimating spreads: {len(symbols)} symbols × {len(months)} months = {total} jobs",
          file=sys.stderr)

    if len(symbols) == 1 or args.workers <= 1:
        pbar = tqdm(total=total, desc="months", unit="mo")
        for sym in symbols:
            for y, m in months:
                res = estimate_one_month(sym, y, m, bucket=args.bucket, force=args.force)
                pbar.update(1)
                if res["status"] == "error":
                    tqdm.write(f"[{sym} {y:04d}-{m:02d}] {res['error']}")
        pbar.close()
    else:
        # Per-symbol parallelism: each symbol-range is a worker. Avoids
        # contention on the same parquet from multiple processes.
        pbar = tqdm(total=total, desc="months", unit="mo")
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {
                ex.submit(estimate_symbol_range, sym, start_ym, end_ym, args.bucket, args.force): sym
                for sym in symbols
            }
            for f in as_completed(futs):
                sym = futs[f]
                try:
                    results = f.result()
                    pbar.update(len(results))
                    for r in results:
                        if r["status"] == "error":
                            tqdm.write(f"[{r['symbol']} {r['month']}] {r['error']}")
                except Exception as e:
                    tqdm.write(f"[{sym}] FAILED: {e}")
        pbar.close()


if __name__ == "__main__":
    main()
