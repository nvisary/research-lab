import subprocess
import sys
import time
from datetime import datetime, timezone

import ccxt


def get_top_50():
    ex = ccxt.bybit()
    markets = ex.load_markets()
    tickers = ex.fetch_tickers()
    cutoff = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    # Filter: linear USDT perps launched before 2024
    valid = []
    for symbol, ticker in tickers.items():
        if not symbol.endswith("/USDT:USDT"):
            continue
        m = markets.get(symbol)
        if not m:
            continue
        launch_time = int(m["info"].get("launchTime", 0) or 0)
        if 0 < launch_time < cutoff:
            valid.append(ticker)

    # Sort by quote volume
    valid.sort(key=lambda x: x["quoteVolume"] or 0, reverse=True)
    return [t["info"]["symbol"] for t in valid[:50]]


def main():
    symbols = get_top_50()
    print(f"Top 50 symbols: {symbols}")

    start = "2024-01"
    end = "2026-05"

    for i, sym in enumerate(symbols):
        print(f"[{i + 1}/50] Processing {sym}...")

        # Download OHLCV with retries
        for attempt in range(3):
            try:
                print(f"  Attempting OHLCV download (attempt {attempt + 1})...")
                subprocess.run(
                    [
                        "uv",
                        "run",
                        "python",
                        "-m",
                        "datafeed.download_bybit",
                        "--symbol",
                        sym,
                        "--start",
                        start,
                        "--end",
                        end,
                    ],
                    check=True,
                )
                break
            except subprocess.CalledProcessError as e:
                print(f"  OHLCV download failed for {sym}: {e}")
                if attempt < 2:
                    print("  Waiting 30s before retry...")
                    time.sleep(30)
                else:
                    print(f"  Skipping OHLCV for {sym} after 3 attempts.")

        # Download Funding with retries
        for attempt in range(3):
            try:
                print(f"  Attempting Funding download (attempt {attempt + 1})...")
                subprocess.run(
                    [
                        "uv",
                        "run",
                        "python",
                        "-m",
                        "datafeed.download_bybit_funding",
                        "--symbol",
                        sym,
                        "--start",
                        start,
                        "--end",
                        end,
                    ],
                    check=True,
                )
                break
            except subprocess.CalledProcessError as e:
                print(f"  Funding download failed for {sym}: {e}")
                if attempt < 2:
                    print("  Waiting 15s before retry...")
                    time.sleep(15)
                else:
                    print(f"  Skipping Funding for {sym} after 3 attempts.")

        # Small cooldown between symbols to avoid aggressive rate limiting
        time.sleep(5)


if __name__ == "__main__":
    main()
