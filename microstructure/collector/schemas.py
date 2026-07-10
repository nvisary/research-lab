"""Stream definitions and row builders.

Each captured stream is a flat table of dict-rows that the writer buffers and
flushes to zstd parquet. Every row carries two timestamps in **milliseconds**:

    ts_exchange -- exchange event time (from the ccxt payload) when available
    ts_local    -- local wall-clock at receipt (used to measure feed lag)

The orderbook stream is stored *wide* (one column per level per side) because
strategy code reads it as a rectangular frame and zstd compresses the highly
correlated price/size columns very well. Depth is fixed per session.
"""
from __future__ import annotations

# Canonical stream names + the CLI aliases that map onto them.
STREAMS: tuple[str, ...] = ("orderbook", "trades", "ticker", "liquidations")

# Bybit swap order-book subscription depths accepted by the WS API. We subscribe
# to the smallest level that covers the requested storage depth, then persist the
# top-N levels from each snapshot.
VALID_OB_SUB_LIMITS: tuple[int, ...] = (1, 50, 200, 1000)


def pick_sub_limit(store_depth: int) -> int:
    for lim in VALID_OB_SUB_LIMITS:
        if lim >= store_depth:
            return lim
    return VALID_OB_SUB_LIMITS[-1]

STREAM_ALIASES: dict[str, str] = {
    "orderbook": "orderbook", "book": "orderbook", "ob": "orderbook", "depth": "orderbook",
    "trades": "trades", "trade": "trades", "tape": "trades",
    "ticker": "ticker", "bbo": "ticker", "tick": "ticker",
    "liquidations": "liquidations", "liq": "liquidations", "liqs": "liquidations",
}


def resolve_streams(tokens: list[str]) -> list[str]:
    """Map user tokens (aliases, any order) to canonical stream names."""
    out: list[str] = []
    for tok in tokens:
        key = tok.strip().lower()
        if key not in STREAM_ALIASES:
            raise ValueError(f"unknown stream '{tok}'. valid: {sorted(set(STREAM_ALIASES))}")
        canon = STREAM_ALIASES[key]
        if canon not in out:
            out.append(canon)
    return out


def orderbook_columns(depth: int) -> list[str]:
    """Wide-format column order for an order-book snapshot of `depth` levels."""
    cols = ["ts_exchange", "ts_local", "symbol"]
    for i in range(depth):
        cols += [f"bid_px_{i}", f"bid_sz_{i}"]
    for i in range(depth):
        cols += [f"ask_px_{i}", f"ask_sz_{i}"]
    return cols


# Column order for the fixed-width streams. Kept explicit so parquet parts are
# schema-stable across a session even if a payload omits an optional field.
TRADE_COLUMNS = ["ts_exchange", "ts_local", "symbol", "price", "amount", "side", "id"]
TICKER_COLUMNS = [
    "ts_exchange", "ts_local", "symbol",
    "last", "bid", "bid_size", "ask", "ask_size",
    "mark", "index", "funding_rate", "open_interest",
    "base_volume", "quote_volume",
]
LIQUIDATION_COLUMNS = ["ts_exchange", "ts_local", "symbol", "side", "price", "amount"]


def columns_for(stream: str, depth: int) -> list[str]:
    if stream == "orderbook":
        return orderbook_columns(depth)
    if stream == "trades":
        return TRADE_COLUMNS
    if stream == "ticker":
        return TICKER_COLUMNS
    if stream == "liquidations":
        return LIQUIDATION_COLUMNS
    raise ValueError(f"unknown stream {stream!r}")


def orderbook_row(ob: dict, symbol: str, depth: int, ts_local: int) -> dict:
    """Flatten a ccxt order book into a wide top-`depth` snapshot row."""
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
    row: dict = {
        "ts_exchange": ob.get("timestamp"),
        "ts_local": ts_local,
        "symbol": symbol,
    }
    for i in range(depth):
        if i < len(bids):
            row[f"bid_px_{i}"] = bids[i][0]
            row[f"bid_sz_{i}"] = bids[i][1]
        else:
            row[f"bid_px_{i}"] = None
            row[f"bid_sz_{i}"] = None
    for i in range(depth):
        if i < len(asks):
            row[f"ask_px_{i}"] = asks[i][0]
            row[f"ask_sz_{i}"] = asks[i][1]
        else:
            row[f"ask_px_{i}"] = None
            row[f"ask_sz_{i}"] = None
    return row


def trade_row(t: dict, symbol: str, ts_local: int) -> dict:
    return {
        "ts_exchange": t.get("timestamp"),
        "ts_local": ts_local,
        "symbol": symbol,
        "price": t.get("price"),
        "amount": t.get("amount"),
        "side": t.get("side"),
        "id": t.get("id"),
    }


def ticker_row(tk: dict, symbol: str, ts_local: int) -> dict:
    info = tk.get("info") or {}
    # ccxt normalises most fields; funding/OI live under info for bybit.
    return {
        "ts_exchange": tk.get("timestamp"),
        "ts_local": ts_local,
        "symbol": symbol,
        "last": tk.get("last"),
        "bid": tk.get("bid"),
        "bid_size": tk.get("bidVolume"),
        "ask": tk.get("ask"),
        "ask_size": tk.get("askVolume"),
        "mark": _to_float(tk.get("markPrice") or info.get("markPrice")),
        "index": _to_float(tk.get("indexPrice") or info.get("indexPrice")),
        "funding_rate": _to_float(info.get("fundingRate")),
        "open_interest": _to_float(info.get("openInterest") or info.get("openInterestValue")),
        "base_volume": tk.get("baseVolume"),
        "quote_volume": tk.get("quoteVolume"),
    }


def liquidation_row(lq: dict, symbol: str, ts_local: int) -> dict:
    return {
        "ts_exchange": lq.get("timestamp"),
        "ts_local": ts_local,
        "symbol": symbol,
        "side": lq.get("side"),
        "price": _to_float(lq.get("price")),
        "amount": _to_float(lq.get("amount") or (lq.get("info") or {}).get("size")),
    }


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
