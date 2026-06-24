"""Funding carry baseline: spot-hedged short high-positive-funding perps.

The economic idea is spot/perp cash-and-carry: buy spot and short the perp when
funding APR is high, then exit when funding normalizes. The harness models the
perp leg directly and adds a synthetic spot hedge when SPOT_HEDGE is enabled.
"""
from __future__ import annotations

import pandas as pd

from datafeed.loader import load_funding


DEFAULT_SYMBOLS: list[str] = [
    "1000PEPEUSDT",
    "AAVEUSDT",
    "ADAUSDT",
    "ALGOUSDT",
    "ARBUSDT",
    "AVAXUSDT",
    "BCHUSDT",
    "BNBUSDT",
    "DASHUSDT",
    "DOGEUSDT",
    "DOTUSDT",
    "ENJUSDT",
    "HBARUSDT",
    "INJUSDT",
    "JTOUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "NEARUSDT",
    "OPUSDT",
    "SOLUSDT",
    "SUIUSDT",
    "TIAUSDT",
    "TONUSDT",
    "TRXUSDT",
    "UNIUSDT",
    "WLDUSDT",
    "XLMUSDT",
    "XMRUSDT",
    "XRPUSDT",
    "ZECUSDT",
]

DEFAULT_TF: str = "1h"

FUNDING_EVENTS_PER_YEAR = 3 * 365

DEFAULT_PARAMS: dict = {
    "entry_apr": 0.50,
    "exit_apr": 0.00,
    "max_gross": 1.0,
}

PARAM_SPACE: dict = {
    "entry_apr": (0.15, 0.80),
    "exit_apr": (0.00, 0.30),
    "max_gross": (0.25, 1.0),
}

RAW_SIZING = True
SPOT_HEDGE = True


def _funding_rate(symbol: str, index: pd.DatetimeIndex) -> pd.Series:
    funding = load_funding(symbol, index[0], index[-1] + pd.Timedelta("8h"))
    if funding.empty:
        return pd.Series(0.0, index=index)
    return funding["rate"].reindex(index, method="ffill").fillna(0.0)


def _target_for_symbol(symbol: str, df: pd.DataFrame, params: dict) -> pd.Series:
    entry_rate = float(params.get("entry_apr", 0.30)) / FUNDING_EVENTS_PER_YEAR
    exit_rate = float(params.get("exit_apr", 0.10)) / FUNDING_EVENTS_PER_YEAR

    funding = _funding_rate(symbol, df.index)
    active = False
    out = []
    for rate in funding.to_numpy():
        if active:
            if rate < exit_rate:
                active = False
        elif rate > entry_rate:
            active = True
        out.append(-1.0 if active else 0.0)
    return pd.Series(out, index=df.index).shift(1).fillna(0.0)


def generate_signals(data: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    frames = []
    raw_positions = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        raw_positions[symbol] = _target_for_symbol(symbol, df, params)

    if not raw_positions:
        return pd.DataFrame(columns=["timestamp", "symbol", "position"])

    gross = pd.concat([s.abs() for s in raw_positions.values()], axis=1).sum(axis=1)
    max_gross = max(float(params.get("max_gross", 1.0)), 0.0)
    scale = (max_gross / gross).clip(upper=1.0).fillna(0.0)

    for symbol, pos in raw_positions.items():
        sized = pos * scale.reindex(pos.index).fillna(0.0)
        frames.append(
            pd.DataFrame(
                {"timestamp": pos.index, "symbol": symbol, "position": sized.values}
            )
        )

    return pd.concat(frames, ignore_index=True)
