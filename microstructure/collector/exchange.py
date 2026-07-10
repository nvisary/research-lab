"""ccxt.pro Bybit client factory + symbol resolution.

A single shared client multiplexes all subscriptions over one WebSocket
connection, which is how ccxt.pro is designed to be used. Symbols are accepted
in exchange-native form (``BTCUSDT``) and converted to ccxt unified linear-swap
notation (``BTC/USDT:USDT``) -- matching the convention already used in
``datafeed.download_bybit``.
"""
from __future__ import annotations

import ccxt.pro as ccxtpro


def make_exchange() -> "ccxtpro.bybit":
    return ccxtpro.bybit({
        "enableRateLimit": True,
        "options": {"defaultType": "swap", "defaultSubType": "linear"},
    })


def to_unified(symbol: str) -> str:
    """BTCUSDT -> BTC/USDT:USDT (ccxt unified linear-swap notation)."""
    if "/" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT:USDT"
    raise ValueError(f"unsupported symbol format: {symbol!r} (expected e.g. BTCUSDT)")


def to_native(unified: str) -> str:
    """BTC/USDT:USDT -> BTCUSDT."""
    if "/" not in unified:
        return unified
    base = unified.split("/")[0]
    return f"{base}USDT"


async def validate_symbols(ex: "ccxtpro.bybit", symbols: list[str]) -> tuple[list[str], list[str]]:
    """Split requested symbols into (valid, unknown) against loaded markets."""
    await ex.load_markets()
    valid, unknown = [], []
    for s in symbols:
        try:
            uni = to_unified(s)
        except ValueError:
            unknown.append(s)
            continue
        if uni in ex.markets:
            valid.append(to_native(uni))
        else:
            unknown.append(s)
    return valid, unknown
