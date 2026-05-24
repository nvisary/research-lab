import glob
import os

import pandas as pd


def analyze_funding_correlation(symbol):
    funding_path = f"data/bybit/perp/funding/{symbol}"
    price_path = f"data/bybit/perp/1m/{symbol}"

    # Load last 6 months of data
    funding_files = sorted(glob.glob(f"{funding_path}/*.parquet"))[-6:]
    price_files = sorted(glob.glob(f"{price_path}/*.parquet"))[-6:]

    funding_df = pd.concat([pd.read_parquet(f) for f in funding_files])
    price_df = pd.concat([pd.read_parquet(f) for f in price_files])

    price_df.index = pd.to_datetime(price_df.index)
    price_1h = price_df.resample("1h").agg({"close": "last"})
    price_1h["returns"] = price_1h["close"].pct_change().shift(-1)  # Forward return

    funding_df = (
        funding_df.set_index("timestamp")
        if "timestamp" in funding_df.columns
        else funding_df
    )
    funding_df.index = pd.to_datetime(funding_df.index)

    df = price_1h.join(funding_df["rate"], how="inner")

    print(f"{symbol} funding index: {funding_df.index[:5]}")
    print(f"{symbol} price index: {price_1h.index[:5]}")
    corr = df["rate"].corr(df["returns"])
    print(f"{symbol} funding-return correlation: {corr:.4f}")


analyze_funding_correlation("BTCUSDT")
analyze_funding_correlation("ETHUSDT")
analyze_funding_correlation("BTCUSDT")
analyze_funding_correlation("ETHUSDT")
